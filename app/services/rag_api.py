import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from concurrent.futures import CancelledError
from contextlib import aclosing
from datetime import date, datetime
from threading import Event
from typing import cast
from typing import Annotated
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from anyio import (
    BrokenResourceError, CancelScope, EndOfStream, create_memory_object_stream,
    fail_after, move_on_after,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.types import Send

from app.agent.conversation import run_agent_turn
from app.agent.qa import EvidenceError, QAResponse, StreamCompletion, StreamEvent
from app.agent.session import SessionBusyError
from app.core.auth import DB, authenticated_user
from app.core.config import settings
from app.db.database import SessionLocal
from app.models import Document, User
from app.rag.embeddings import EmbeddingInputError
from app.rag.hybrid import HybridRanker
from app.rag.keyword import KeywordRanker
from app.rag.retrieval import RetrievalScope
from app.rag.vector import VectorRanker
from app.services.deepseek_client import DeepSeekError, stream_json


router = APIRouter()
logger = logging.getLogger(__name__)
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
ABSOLUTE_DATE = re.compile(
    r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"
    r"|(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})[日号]"
)
NAMED_DOCUMENT = re.compile(r"《([^》]+)》")
DOCUMENT_REQUEST = re.compile(r"请(?:查询|查阅|提供|查看)\s*(?:公司)?")


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    as_of_date: date | None = None
    document_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)] | None = None
    session_id: UUID | None = None
    new_question: bool = False


class AskResponse(QAResponse):
    session_id: UUID
    awaiting_clarification: bool


def resolve_query_date(payload: AskRequest) -> date:
    if payload.as_of_date is not None:
        return payload.as_of_date
    try:
        dates = {
            date(*map(int, match[:3] if match[0] else match[3:]))
            for match in ABSOLUTE_DATE.findall(payload.question)
        }
    except ValueError:
        raise HTTPException(422, "问题中的日期无效，请指定 as_of_date") from None
    if len(dates) > 1:
        raise HTTPException(422, "问题包含多个日期，请指定 as_of_date")
    return next(iter(dates)) if dates else datetime.now(BUSINESS_TIMEZONE).date()


def resolve_document_id(db: Session, payload: AskRequest) -> str | None:
    if payload.document_id is not None:
        return payload.document_id
    names = set(NAMED_DOCUMENT.findall(payload.question))
    starts = [m.end() for m in DOCUMENT_REQUEST.finditer(payload.question)]
    if not names and not starts:
        return None

    matches = {
        doc_id for doc_id, title in db.execute(select(Document.id, Document.title))
        if title in names or doc_id in names
        or any(payload.question.startswith((title, doc_id), start) for start in starts)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def run_ask(
    payload: AskRequest,
    request: Request,
    db: DB,
    user_id: str,
    *,
    stream_completion: StreamCompletion | None = None,
    emit: StreamEvent | None = None,
) -> AskResponse:
    def rank(query: str, scope: RetrievalScope, limit: int):
        client = request.app.state.qdrant
        if client is None:
            raise EvidenceError("向量索引未就绪")
        return HybridRanker(KeywordRanker(db), VectorRanker(client))(query, scope, limit)

    session_id = payload.session_id or uuid4()
    try:
        result, awaiting = run_agent_turn(
            db, rank, request.app.state.clarifications, (user_id, session_id), payload.question,
            payload.new_question, lambda: (resolve_query_date(payload), resolve_document_id(db, payload)),
            stream_completion=stream_completion, emit=emit,
        )
        return AskResponse(**result.model_dump(), session_id=session_id, awaiting_clarification=awaiting)
    except SessionBusyError:
        raise HTTPException(409, "此会话正在处理上一条消息，请稍后重试") from None
    except EmbeddingInputError as exc:
        raise HTTPException(422, str(exc)) from None
    except EvidenceError:
        raise HTTPException(503, "检索服务不可用") from None
    except DeepSeekError:
        raise HTTPException(502, "模型服务不可用或返回了无效回答") from None


@router.post("/rag/ask", response_model=AskResponse)
def ask(
    payload: AskRequest,
    request: Request,
    db: DB,
    user: Annotated[User, Depends(authenticated_user)],
) -> AskResponse:
    return run_ask(payload, request, db, user.id)


class AskStreamResponse(StreamingResponse):
    async def stream_response(self, send: Send) -> None:
        async with aclosing(cast(AsyncGenerator[str, None], self.body_iterator)):
            await super().stream_response(send)


async def stream_ask_events(payload: AskRequest, request: Request, user_id: str) -> AsyncGenerator[str, None]:
    send, receive = create_memory_object_stream[tuple[str, dict]](16)
    loop = asyncio.get_running_loop()
    closed = Event()
    upstream_task = None

    async def send_draft(text: str) -> None:
        await send.send(("draft_delta", {"text": text}))

    async def complete(*, system_prompt: str, user_payload: dict, show_draft: bool) -> dict:
        nonlocal upstream_task
        if closed.is_set():
            raise asyncio.CancelledError
        upstream_task = asyncio.current_task()
        try:
            with fail_after(settings.deepseek_timeout_seconds):
                result = await stream_json(
                    system_prompt=system_prompt, user_payload=user_payload,
                    on_draft=send_draft if show_draft else None,
                )
                if show_draft:
                    await send.send(("phase", {"stage": "reviewing"}))
        except TimeoutError:
            raise DeepSeekError("DeepSeek生成超时") from None
        finally:
            upstream_task = None
        return result

    def complete_in_thread(**kwargs) -> dict:
        if closed.is_set():
            raise CancelledError
        return asyncio.run_coroutine_threadsafe(complete(**kwargs), loop).result()

    def emit(event: str, data: dict) -> None:
        if closed.is_set():
            raise CancelledError
        asyncio.run_coroutine_threadsafe(send.send((event, data)), loop).result()

    def produce() -> tuple[str, dict]:
        try:
            with SessionLocal() as db:
                result = run_ask(
                    payload, request, db, user_id,
                    stream_completion=complete_in_thread,
                    emit=emit,
                )
            return "final", result.model_dump(mode="json")
        except (CancelledError, BrokenResourceError):
            raise
        except HTTPException as exc:
            return "error", {"status": exc.status_code, "message": exc.detail}
        except Exception:
            logger.exception("Unexpected error processing a streaming question")
            return "error", {"status": 500, "message": "问答服务暂时不可用"}

    async def run_producer() -> None:
        with send:
            try:
                terminal = await asyncio.to_thread(produce)
                await send.send(terminal)
            except (CancelledError, BrokenResourceError):
                return

    task = asyncio.create_task(run_producer())
    try:
        yield 'event: phase\ndata: {"stage":"searching"}\n\n'
        while True:
            with move_on_after(10) as heartbeat:
                try:
                    event, data = await receive.receive()
                except EndOfStream:
                    break
            if heartbeat.cancelled_caught:
                yield ": keep-alive\n\n"
            else:
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
    finally:
        closed.set()
        receive.close()
        tasks = [task]
        if upstream_task is not None:
            tasks.append(upstream_task)
        for pending in tasks:
            pending.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)


@router.post("/rag/ask/stream")
def ask_stream(
    payload: AskRequest,
    request: Request,
    user: Annotated[User, Depends(authenticated_user)],
) -> StreamingResponse:
    return AskStreamResponse(
        stream_ask_events(payload, request, user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
