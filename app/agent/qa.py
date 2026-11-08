import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from time import perf_counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.clarification import clarify_missing_condition
from app.agent.conflict_followup import COMPARISON, Policy, find_missing_relations
from app.agent.prompts import DEFAULT_PROMPT_VERSION, SYSTEM_PROMPTS
from app.models import DocumentChunk
from app.rag.retrieval import Ranker, RetrievalHit, RetrievalScope, resolve_scope
from app.services.deepseek_client import DeepSeekError, ModelJSONError, complete_json


TOP_K = 10
EVIDENCE_CHAR_BUDGET = 6000
CITATION_MARKER = re.compile(r"\[(E[0-9]+)\]")
logger = logging.getLogger(__name__)
FORMAT_CORRECTION = """
上一次输出未通过格式或引用校验。仍基于原问题和原证据作答。
只输出含 status、answer、citation_ids 三个字段的 JSON 对象，不增加其他字段。
status 只能是 answer、partial_answer、clarify、insufficient_evidence 或 report_conflict。
answer 为非空中文字符串，citation_ids 为字符串数组，例如 ["E1","E2"]。
正文的 [E1] 标记与 citation_ids 一致，只可引用本轮证据，报告冲突须引用双方。
校验错误（字段和类型）："""

ModelStatus = Literal[
    "answer", "partial_answer", "clarify", "insufficient_evidence", "report_conflict"
]
StreamEvent = Callable[[str, dict], None]
StreamCompletion = Callable[..., dict]


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ModelStatus
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    citation_ids: list[str]


class Citation(BaseModel):
    evidence_id: str
    chunk_id: str
    document_id: str
    version_id: str
    title: str
    version: str
    sections: list[str]
    clause_numbers: list[int]
    source_format: str
    locations: list[dict[str, int | None]]


class QAResponse(BaseModel):
    status: ModelStatus | Literal["access_denied"]
    answer: str
    as_of_date: date
    citations: list[Citation]


class EvidenceError(RuntimeError):
    pass


class AnswerOutputError(DeepSeekError):
    def __init__(self, message: str, issues: list[dict[str, str]]):
        super().__init__(message)
        self.issues = issues


@dataclass
class QATrace:
    eligible_versions: list[str] = field(default_factory=list)
    retrieved_chunks: list[str] = field(default_factory=list)
    context_chunks: dict[str, str] = field(default_factory=dict)
    model_called: bool = False
    reported_model: str | None = None
    usage: dict | None = None
    answer_attempts: int = 0
    answer_attempt_seconds: list[float] = field(default_factory=list)
    answer_format_errors: list[list[dict[str, str]]] = field(default_factory=list)
    followup_versions: list[str] = field(default_factory=list)
    followup_chunks: list[str] = field(default_factory=list)
    conflict_followup: str | None = None
    clarification_rule: str | None = None
    clarification_chunks: dict[str, str] = field(default_factory=dict)


def validation_issues(exc: ValidationError, fields: set[str]) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(
                str(part) if isinstance(part, int) or part in fields else "<unknown>"
                for part in issue["loc"]
            ) or "<root>",
            "type": issue["type"],
        }
        for issue in exc.errors(include_input=False, include_context=False, include_url=False)
    ]


def validate_answer(
    data: dict, sources: dict[str, Citation],
) -> tuple[ModelAnswer, list[Citation]]:
    try:
        output = ModelAnswer.model_validate(data)
    except ValidationError as exc:
        raise AnswerOutputError(
            "模型问答输出结构无效", validation_issues(exc, {"status", "answer", "citation_ids"}),
        ) from None
    cited_ids = set(output.citation_ids)
    markers = list(dict.fromkeys(CITATION_MARKER.findall(output.answer)))
    if cited_ids != set(markers) or not cited_ids <= sources.keys():
        raise AnswerOutputError(
            "模型引用不属于本轮证据或与正文不一致",
            [{"field": "citation_ids", "type": "invalid_or_mismatched_references"}],
        )
    if output.status in {"answer", "partial_answer", "report_conflict"} and not markers:
        raise AnswerOutputError("模型回答缺少证据引用", [{"field": "citation_ids", "type": "references_required"}])
    citations = [sources[evidence_id] for evidence_id in markers]
    if output.status == "report_conflict" and len({c.version_id for c in citations}) < 2:
        raise AnswerOutputError("冲突回答缺少双方制度证据", [{"field": "citation_ids", "type": "conflict_versions_required"}])
    return output, citations


def generate_answer(
    system_prompt: str, payload: dict, sources: dict[str, Citation],
    trace: QATrace | None,
    *,
    stream_completion: StreamCompletion | None = None,
    emit: StreamEvent | None = None,
    show_draft: bool = True,
) -> tuple[ModelAnswer, list[Citation]]:
    prompt = system_prompt
    for attempt in (1, 2):
        started = perf_counter()
        if trace is not None:
            trace.answer_attempts += 1
        try:
            if stream_completion is None:
                result = complete_json(system_prompt=prompt, user_payload=payload)
            else:
                result = stream_completion(system_prompt=prompt, user_payload=payload, show_draft=show_draft)
            if trace is not None:
                trace.reported_model = result.get("reported_model")
                trace.usage = result.get("usage")
            return validate_answer(result["data"], sources)
        except (AnswerOutputError, ModelJSONError) as exc:
            issues = exc.issues if isinstance(exc, AnswerOutputError) else [
                {"field": "<root>", "type": exc.code}
            ]
            logger.warning("RAG answer validation failed on attempt %s: %s", attempt, issues)
            if trace is not None:
                trace.answer_format_errors.append(issues)
            if emit is not None and show_draft:
                emit("draft_reset", {})
            if attempt == 2:
                raise
            prompt = system_prompt + FORMAT_CORRECTION + json.dumps(issues, ensure_ascii=False)
        finally:
            if trace is not None:
                trace.answer_attempt_seconds.append(round(perf_counter() - started, 3))
    raise AssertionError("answer attempts exhausted")


def assemble_evidence(
    db: Session, hits: list[RetrievalHit], scope: RetrievalScope,
) -> tuple[list[dict], dict[str, Citation]]:
    chunks = {
        chunk.id: chunk for chunk in db.scalars(
            select(DocumentChunk).where(DocumentChunk.id.in_([hit.chunk_id for hit in hits]))
        )
    } if hits else {}
    remaining = EVIDENCE_CHAR_BUDGET
    evidence = []
    sources: dict[str, Citation] = {}
    for hit in hits:
        chunk = chunks.get(hit.chunk_id)
        if chunk is None or chunk.version_id != hit.version_id or chunk.version_id not in scope.version_ids:
            raise EvidenceError("检索结果与授权范围或切片库不一致")
        if len(chunk.content) > remaining:
            break
        remaining -= len(chunk.content)
        metadata = chunk.chunk_metadata
        evidence_id = f"E{len(evidence) + 1}"
        evidence.append({
            "id": evidence_id,
            "title": metadata["title"],
            "version": metadata["version"],
            "sections": metadata["sections"],
            "clause_numbers": metadata["clause_numbers"],
            "text": chunk.content,
        })
        locations = []
        for span in metadata["source_spans"]:
            locator = span["locator"]
            if locator not in locations:
                locations.append(locator)
        sources[evidence_id] = Citation(
            evidence_id=evidence_id, chunk_id=chunk.id,
            document_id=metadata["document_id"], version_id=chunk.version_id,
            title=metadata["title"], version=metadata["version"],
            sections=metadata["sections"], clause_numbers=metadata["clause_numbers"],
            source_format=metadata["source_format"], locations=locations,
        )
    return evidence, sources


def answer_question(
    db: Session,
    rank: Ranker,
    *,
    user_id: str,
    question: str,
    as_of_date: date,
    document_id: str | None = None,
    trace: QATrace | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    enable_conflict_followup: bool = False,
    enable_clarification_rules: bool = False,
    stream_completion: StreamCompletion | None = None,
    emit: StreamEvent | None = None,
) -> QAResponse:
    scope = resolve_scope(db, user_id, as_of_date, document_id)
    if trace is not None:
        trace.eligible_versions = sorted(scope.version_ids)
    if scope.status == "access_denied":
        return QAResponse(
            status="access_denied", answer="你无权访问指定制度。",
            as_of_date=as_of_date, citations=[],
        )

    hits = rank(question, scope, TOP_K) if scope.version_ids else []
    if trace is not None:
        trace.retrieved_chunks = [hit.chunk_id for hit in hits]
    evidence, sources = assemble_evidence(db, hits, scope)

    if not evidence:
        return QAResponse(
            status="insufficient_evidence", answer="未找到足以回答该问题的可用制度证据。",
            as_of_date=as_of_date, citations=[],
        )

    if enable_clarification_rules:
        clarification = clarify_missing_condition(
            question, evidence, {key: source.version_id for key, source in sources.items()},
        )
        if clarification is not None:
            output, citations = validate_answer(
                {
                    "status": "clarify", "answer": clarification.answer,
                    "citation_ids": clarification.citation_ids,
                },
                sources,
            )
            if trace is not None:
                trace.clarification_rule = clarification.rule
                trace.clarification_chunks = {c.evidence_id: c.chunk_id for c in citations}
            return QAResponse(
                status=output.status, answer=output.answer, as_of_date=as_of_date, citations=citations,
            )

    system_prompt = SYSTEM_PROMPTS[prompt_version]
    payload = {"question": question, "as_of_date": as_of_date.isoformat(), "evidence": evidence}

    logger.info("RAG context chunk IDs: %s", [source.chunk_id for source in sources.values()])
    if trace is not None:
        trace.context_chunks = {key: source.chunk_id for key, source in sources.items()}
        trace.model_called = True
    if emit is not None:
        emit("phase", {"stage": "generating"})
    output, citations = generate_answer(
        system_prompt, payload, sources, trace, stream_completion=stream_completion, emit=emit,
    )

    if enable_conflict_followup and output.status == "report_conflict" and COMPARISON.search(question):
        policies = {
            c.version_id: Policy(c.document_id, c.title)
            for c in citations if c.version_id in scope.version_ids
        }
        outcome, additions = find_missing_relations(
            db, policies,
            [(sources[item["id"]].version_id, item["text"]) for item in evidence],
            {hit.chunk_id for hit in hits},
        )
        if trace is not None:
            trace.conflict_followup = outcome
            if outcome != "no_missing_relation":
                trace.followup_versions = sorted(policies)
                trace.followup_chunks = [hit.chunk_id for hit in additions]
        if additions:
            expanded, expanded_sources = assemble_evidence(db, hits + additions, scope)
            new_ids = expanded_sources.keys() - sources.keys()
            if new_ids:
                if trace is not None:
                    trace.context_chunks = {key: src.chunk_id for key, src in expanded_sources.items()}
                try:
                    revised, revised_citations = generate_answer(
                        system_prompt, {**payload, "evidence": expanded}, expanded_sources, trace,
                        stream_completion=stream_completion, show_draft=False,
                    )
                except DeepSeekError:
                    if trace is not None:
                        trace.conflict_followup = "revision_failed"
                else:
                    if any(c.evidence_id in new_ids for c in revised_citations):
                        output, citations, sources = revised, revised_citations, expanded_sources
                        if trace is not None:
                            trace.conflict_followup = "used_relation"
                    elif trace is not None:
                        trace.conflict_followup = "relation_not_cited"
            elif trace is not None:
                trace.conflict_followup = "context_budget"

    logger.info(
        "RAG result status=%s retrieved=%s context=%s cited=%s",
        output.status, [hit.chunk_id for hit in hits],
        [source.chunk_id for source in sources.values()],
        [citation.chunk_id for citation in citations],
    )
    return QAResponse(
        status=output.status, answer=output.answer, as_of_date=as_of_date, citations=citations,
    )
