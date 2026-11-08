import re
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from time import monotonic

from sqlalchemy.orm import Session

from app.agent.prompts import DEFAULT_PROMPT_VERSION
from app.agent.qa import QAResponse, QATrace, StreamCompletion, StreamEvent, answer_question
from app.agent.session import (
    CLARIFICATION_TTL_SECONDS, MAX_UNCLEAR_REPLIES, ClarificationStore,
    PendingClarification, SessionKey,
)
from app.rag.retrieval import Ranker


FIELDS = {
    "payment_total": ("同一合同或同一事项的累计应付金额", "6000 元"),
    "procurement_total": ("同一需求的累计采购金额", "6000 元"),
    "leave_workdays": ("连续请假的工作日数", "4 个工作日"),
}
ARABIC = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
CHINESE = r"[一二两三四五六七八九十][零〇一二两三四五六七八九十百千万亿]*(?:点[零〇一二三四五六七八九]+)?"
MONEY_VALUE = re.compile(rf"(?:(?:人民币|¥)\s*)?({ARABIC}|{CHINESE})\s*(万元|万|亿元|亿|元|块钱|块)?")
WORKDAY_VALUE = re.compile(rf"({ARABIC}|{CHINESE}|半)\s*(?:个?工作日)?")
REPLY_PREFIX = re.compile(r"^(?:(?:同一需求|同一合同|同一事项|这次|这笔)?\s*(?:累计采购金额|累计应付金额|累计金额|金额|工作日数|请假天数)|一共|合计|总共|累计|连续请假|请假|请|那如果是|那就|那|如果是)\s*(?:为|是|[:：])?\s*|^是\s*")
QUESTION_CUE = re.compile(r"怎么|如何|什么|哪[些个份]|多少|几家|多久|何时|谁|能否|能不能|可不可以|是否|能报|吗[?？]?$|[?？]$")
UNCLEAR_REPLY = re.compile(r"^(?:大概|差不多|不知道|不清楚|没算|待定|不确定|可以|行|嗯|好|对|是的)(?:吧|吗|呢)?[?？。！!]*$")
VALUE_CONFIRMATION = re.compile(r"(?:可以|行|够|对|是|算数|合适)吗[?？。]*$")


def parse_condition(question: str, rule: str) -> str | None:
    value = unicodedata.normalize("NFKC", question).strip().rstrip("。！!，,?？").removesuffix("呢").strip()
    value = REPLY_PREFIX.sub("", value, count=1)
    duration = rule == "leave_workdays"
    match = (WORKDAY_VALUE if duration else MONEY_VALUE).fullmatch(value)
    if not match:
        return None
    number = match[1]
    if number[0].isascii() and Decimal(number.replace(",", "")) <= 0:
        return None
    if duration:
        return f"{number}个工作日"
    unit = match[2] or "元"
    unit = {"万": "万元", "亿": "亿元", "块": "元", "块钱": "元"}.get(unit, unit)
    return f"{number}{unit}"


def is_new_question(question: str) -> bool:
    confirmation = VALUE_CONFIRMATION.search(question)
    if confirmation and any(
        parse_condition(question[:confirmation.start()], rule)
        for rule in ("payment_total", "leave_workdays")
    ):
        return False
    return len(question) >= 6 and not UNCLEAR_REPLY.fullmatch(question) and bool(QUESTION_CUE.search(question))


def respond_to_turn(
    store: ClarificationStore,
    key: SessionKey,
    question: str,
    new_question: bool,
    resolve_query: Callable[[], tuple[date, str | None]],
    answer: Callable[[str, date, str | None, QATrace], QAResponse],
    trace: QATrace | None = None,
) -> tuple[QAResponse, bool]:
    with store.turn(key) as pending:
        value = parse_condition(question, pending.rule) if pending is not None and not new_question else None
        if new_question or (pending is not None and value is None and is_new_question(question)):
            store.clear(key)
            pending = None
        if pending is not None:
            if value is None:
                attempts = pending.unclear_replies + 1
                awaiting = attempts < MAX_UNCLEAR_REPLIES
                if awaiting:
                    store.save(key, replace(pending, unclear_replies=attempts))
                    label, example = FIELDS[pending.rule]
                    message = f"请回复{label}，例如「{example}」。"
                else:
                    store.clear(key)
                    message = "暂未确认所需条件，请把原问题和补充条件一起重发。"
                return QAResponse(
                    status="clarify", answer=message, as_of_date=pending.as_of_date, citations=[],
                ), awaiting
            label, _ = FIELDS[pending.rule]
            question = f"{pending.question}\n补充条件：{label}为{value}。"
            query_date, document_id = pending.as_of_date, pending.document_id
        else:
            query_date, document_id = resolve_query()
            if not new_question and (
                parse_condition(question, "payment_total") or parse_condition(question, "leave_workdays")
            ):
                return QAResponse(
                    status="clarify", answer="请把要咨询的问题和这个条件一起发送。",
                    as_of_date=query_date, citations=[],
                ), False

        trace = trace if trace is not None else QATrace()
        response = answer(question, query_date, document_id, trace)
        if response.status == "clarify" and trace.clarification_rule in FIELDS:
            store.save(key, PendingClarification(
                question, trace.clarification_rule, query_date, document_id,
                monotonic() + CLARIFICATION_TTL_SECONDS,
            ))
            return response, True
        store.clear(key)
        return response, False


def run_agent_turn(
    db: Session, rank: Ranker, store: ClarificationStore, key: SessionKey,
    question: str, new_question: bool, resolve_query: Callable[[], tuple[date, str | None]],
    trace: QATrace | None = None,
    *,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    stream_completion: StreamCompletion | None = None,
    emit: StreamEvent | None = None,
) -> tuple[QAResponse, bool]:
    def answer(query: str, query_date: date, document_id: str | None, turn_trace: QATrace) -> QAResponse:
        return answer_question(
            db, rank, user_id=key[0], question=query, as_of_date=query_date, document_id=document_id,
            trace=turn_trace, prompt_version=prompt_version,
            enable_clarification_rules=True, enable_conflict_followup=True,
            stream_completion=stream_completion, emit=emit,
        )

    return respond_to_turn(store, key, question, new_question, resolve_query, answer, trace)
