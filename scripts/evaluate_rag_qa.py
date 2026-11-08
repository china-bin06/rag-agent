import argparse
import hashlib
import json
import time
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict
from datetime import date
from pathlib import Path
from uuid import uuid4

from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.agent.conflict_followup import MAX_RELATION_CHUNKS
from app.agent.conversation import run_agent_turn
from app.agent.prompts import DEFAULT_PROMPT_VERSION, SYSTEM_PROMPTS
from app.agent.qa import EVIDENCE_CHAR_BUDGET, TOP_K, EvidenceError, QATrace, answer_question
from app.agent.session import ClarificationStore, SessionKey
from app.core.config import settings
from app.db.database import SessionLocal
from app.models import DocumentChunk
from app.rag.hybrid import BRANCH_DEPTH, RRF_K, HybridRanker
from app.rag.keyword import KeywordRanker
from app.rag.retrieval_eval import map_evidence, normalize_quote
from app.rag.vector import QDRANT_PATH, VectorRanker, validate_vector_index
from app.services.deepseek_client import DeepSeekError


ROOT = Path(__file__).resolve().parents[1]
FOLLOWUP_CASES = {
    "Q017": ("20000 元", "answer"),
    "Q018": ("4 个工作日", "report_conflict"),
    "Q019": ("6000 元", "answer"),
}


def run_question(
    db, ranker, question: dict, relevant: dict, prompt_version: str,
    enable_conflict_followup: bool,
    enable_clarification_rules: bool,
    store: ClarificationStore | None = None,
    key: SessionKey | None = None,
) -> dict:
    started = time.perf_counter()
    query_date = date.fromisoformat(question["as_of_date"])
    document_id = question.get("access_target_document_id") if question["query_mode"] == "document" else None
    awaiting = False
    for attempt in range(1, 4):
        trace = QATrace()
        try:
            if store is not None and key is not None:
                response, awaiting = run_agent_turn(
                    db, ranker, store, key, question["question"], False,
                    lambda: (query_date, document_id), trace, prompt_version=prompt_version,
                )
            else:
                response = answer_question(
                    db, ranker, user_id=question["user_id"], question=question["question"],
                    as_of_date=query_date, document_id=document_id, trace=trace,
                    prompt_version=prompt_version,
                    enable_conflict_followup=enable_conflict_followup,
                    enable_clarification_rules=enable_clarification_rules,
                )
            result, error = response.model_dump(mode="json"), None
            break
        except (DeepSeekError, EvidenceError) as exc:
            result, error = None, str(exc)
            transient = isinstance(exc, DeepSeekError) and any(
                code in error for code in ("网络请求失败", "请求超时", "HTTP 429", "HTTP 5")
            )
            if not transient or attempt == 3:
                break
            time.sleep(2 ** (attempt - 1))

    return {
        "question_id": question["question_id"],
        "expected_status": question["expected_behavior"],
        "category": question["category"],
        "relevant_chunks": relevant,
        "trace": asdict(trace),
        "response": result,
        "error": error,
        "attempts": attempt,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "awaiting_clarification": awaiting,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full RAG answer evaluation")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-version", choices=SYSTEM_PROMPTS, default=DEFAULT_PROMPT_VERSION)
    parser.add_argument("--prioritize", nargs="+", default=[], metavar="QUESTION_ID")
    parser.add_argument("--questions", nargs="+", metavar="QUESTION_ID")
    parser.add_argument("--conflict-followup", action="store_true")
    parser.add_argument("--clarification-rules", action="store_true")
    parser.add_argument("--current-agent", action="store_true")
    parser.add_argument("--followups-output", type=Path)
    args = parser.parse_args()
    if args.current_agent and (args.conflict_followup or args.clarification_rules):
        parser.error("--current-agent already enables conflict follow-up and clarification")
    if args.followups_output and not args.current_agent:
        parser.error("--followups-output requires --current-agent")
    if not settings.deepseek_api_key.get_secret_value().strip():
        parser.error("DEEPSEEK_API_KEY is required")

    raw = (ROOT / "questions.jsonl").read_bytes()
    questions = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    priority_ids = set(args.prioritize)
    selected_ids = set(args.questions or [])
    if unknown_ids := (priority_ids | selected_ids) - {q["question_id"] for q in questions}:
        parser.error(f"Unknown question IDs: {', '.join(sorted(unknown_ids))}")
    if selected_ids:
        questions = [q for q in questions if q["question_id"] in selected_ids]
    questions.sort(key=lambda question: question["question_id"] not in priority_ids)
    with SessionLocal() as db, closing(QdrantClient(path=str(QDRANT_PATH))) as client:
        validate_vector_index(client, db)
        chunks_by_version = defaultdict(list)
        corpus_hash = hashlib.sha256()
        for chunk in db.scalars(
            select(DocumentChunk).options(joinedload(DocumentChunk.document_version))
            .order_by(DocumentChunk.id)
        ):
            digest = hashlib.sha256(chunk.content.encode()).hexdigest()
            if digest != chunk.content_sha256:
                raise ValueError(f"Invalid content hash for {chunk.id}")
            corpus_hash.update(f"{chunk.id} {chunk.version_id} {digest}\n".encode())
            chunks_by_version[chunk.version_id].append((chunk, normalize_quote(chunk.content)))

        labels = [map_evidence(q, chunks_by_version) for q in questions]
        metadata = {
            "dataset": "questions.jsonl",
            "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "corpus_sha256": corpus_hash.hexdigest(),
            "question_count": len(questions),
            "requested_model": settings.deepseek_model,
            "prompt_version": args.prompt_version,
            "prompt_sha256": hashlib.sha256(SYSTEM_PROMPTS[args.prompt_version].encode()).hexdigest(),
            "conflict_followup": {
                "method": "cited_versions_relation_scan",
                "max_new_chunks": MAX_RELATION_CHUNKS,
            } if args.conflict_followup or args.current_agent else None,
            "clarification_rules": args.clarification_rules or args.current_agent,
            "current_agent": args.current_agent,
            "strategy": {
                "top_k": TOP_K, "branch_depth": BRANCH_DEPTH, "rrf_k": RRF_K,
                "evidence_char_budget": EVIDENCE_CHAR_BUDGET,
            },
        }
        ranker = HybridRanker(KeywordRanker(db), VectorRanker(client))
        store = ClarificationStore() if args.current_agent else None
        followups = []
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps({"run": metadata}, ensure_ascii=False) + "\n")
            for question, relevant in zip(questions, labels, strict=True):
                key = (question["user_id"], uuid4()) if store is not None else None
                row = run_question(
                    db, ranker, question, relevant, args.prompt_version,
                    args.conflict_followup,
                    args.clarification_rules,
                    store, key,
                )
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                response = row["response"]
                print(f"{row['question_id']}: {response['status'] if response else row['error']}", flush=True)
                if args.followups_output and question["question_id"] in FOLLOWUP_CASES:
                    reply, expected = FOLLOWUP_CASES[question["question_id"]]
                    case = {**question, "question": reply, "expected_behavior": expected}
                    followup = run_question(db, ranker, case, {}, args.prompt_version, False, False, store, key)
                    followup["reply"] = reply
                    followups.append(followup)
                    print(f"{row['question_id']} follow-up: {(followup['response'] or {}).get('status') or followup['error']}", flush=True)
        if args.followups_output:
            with args.followups_output.open("x", encoding="utf-8") as output:
                for row in followups:
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
