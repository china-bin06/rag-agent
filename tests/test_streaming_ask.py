import asyncio
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.agent.qa import generate_answer
from app.core.auth import authenticated_user
from app.services.answer_draft import AnswerDraft
from app.services.rag_api import AskRequest, AskResponse, router, stream_ask_events


class AnswerDraftTest(unittest.TestCase):
    def test_extracts_answer_across_escape_boundaries(self):
        answer = '依据\n第十二条 [E1]，"符合条件" 🌟。'
        for fields in (
            {"status": "answer", "answer": answer, "citation_ids": ["E1"]},
            {"answer": answer, "citation_ids": ["E1"], "status": "answer"},
        ):
            draft = AnswerDraft()
            payload = json.dumps(fields)
            self.assertEqual("".join(draft.feed(char) for char in payload), answer)

    def test_only_reads_top_level_answer_string(self):
        draft = AnswerDraft()
        self.assertEqual(draft.feed('{"extra":{"answer":"ignored"},"answer":"正文'), "正文")
        self.assertEqual(draft.feed('","citation_ids":[]}'), "")

    def test_invalid_draft_is_reset_before_format_retry(self):
        responses = iter([
            {"status": "answer", "answer": "Invalid [E9]", "citation_ids": ["E9"]},
            {"status": "insufficient_evidence", "answer": "证据不足", "citation_ids": []},
        ])
        events = []

        def complete(**kwargs):
            response = next(responses)
            events.append(("draft_delta", {"text": response["answer"]}))
            return {"data": response}

        result, _ = generate_answer(
            "prompt", {}, {}, None, stream_completion=complete,
            emit=lambda event, data: events.append((event, data)),
        )
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertEqual([name for name, _ in events], ["draft_delta", "draft_reset", "draft_delta"])


class StreamRouteTest(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router)

    def test_requires_authentication_before_streaming(self):
        def reject():
            raise HTTPException(401, "登录凭证无效")

        self.app.dependency_overrides[authenticated_user] = reject
        response = TestClient(self.app).post("/rag/ask/stream", json={"question": "怎么办？"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "登录凭证无效"})

    def test_emits_draft_then_formal_answer_with_same_session(self):
        self.app.dependency_overrides[authenticated_user] = lambda: SimpleNamespace(id="test-user")
        session_id = uuid4()

        async def upstream(*, system_prompt, user_payload, on_draft):
            await on_draft("证据")
            return {"data": {"answer": "证据不足"}}

        def answer(payload, request, db, user_id, *, stream_completion, emit):
            self.assertEqual(user_id, "test-user")
            emit("phase", {"stage": "generating"})
            result = stream_completion(system_prompt="prompt", user_payload={}, show_draft=True)
            return AskResponse(
                status="insufficient_evidence", answer=result["data"]["answer"],
                as_of_date=date(2026, 11, 8), citations=[], session_id=payload.session_id,
                awaiting_clarification=False,
            )

        with patch("app.services.rag_api.run_ask", side_effect=answer), patch(
            "app.services.rag_api.stream_json", side_effect=upstream,
        ):
            response = TestClient(self.app).post(
                "/rag/ask/stream", json={"question": "怎么办？", "session_id": str(session_id)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        frames = [
            (event.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
            for event, data in (frame.splitlines() for frame in response.text.strip().split("\n\n"))
        ]
        self.assertEqual([name for name, _ in frames], ["phase", "phase", "draft_delta", "phase", "final"])
        self.assertEqual(frames[2][1]["text"], "证据")
        self.assertEqual(frames[-1][1]["session_id"], str(session_id))
        self.assertEqual(frames[-1][1]["answer"], "证据不足")


class StreamCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_closing_stream_cancels_in_flight_upstream(self):
        upstream_closed = asyncio.Event()

        async def upstream(*, system_prompt, user_payload, on_draft):
            try:
                await on_draft("草稿")
                await asyncio.Future()
            finally:
                upstream_closed.set()

        def answer(payload, request, db, user_id, *, stream_completion, emit):
            return stream_completion(system_prompt="prompt", user_payload={}, show_draft=True)

        with patch("app.services.rag_api.run_ask", side_effect=answer), patch(
            "app.services.rag_api.stream_json", side_effect=upstream,
        ):
            events = stream_ask_events(AskRequest(question="怎么办？"), None, "test-user")
            async with asyncio.timeout(5):
                self.assertIn("event: phase", await anext(events))
                self.assertIn("event: draft_delta", await anext(events))
                await events.aclose()
                self.assertTrue(upstream_closed.is_set())
