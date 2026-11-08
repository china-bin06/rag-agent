import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import DocumentChunk
from app.rag.retrieval import Ranker, resolve_scope


TOP_K = (3, 5, 10)
SCORED_BEHAVIORS = {"answer", "partial_answer", "report_conflict"}
DEFAULT_RETRIEVAL_DATASET = Path(__file__).resolve().parents[2] / "retrieval_questions.json"


def normalize_quote(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def matches_locator(evidence: dict, metadata: dict) -> bool:
    if evidence["section"] not in metadata["sections"]:
        return False
    clause = evidence["clause_number"]
    if clause is not None and clause not in metadata["clause_numbers"]:
        return False

    locators = [span["locator"] for span in metadata["source_spans"]]
    locator_type = evidence["locator_type"]
    if locator_type == "pdf_page_section_clause":
        return any(
            loc["page_start"] is not None
            and loc["page_start"] <= evidence["page"] <= loc["page_end"]
            for loc in locators
        )
    if locator_type == "docx_paragraph_section_clause":
        return any(
            loc["paragraph_index"] == evidence["paragraph_index"]
            for loc in locators
        )
    if locator_type == "markdown_line_section_clause":
        return any(
            loc["line_start"] is not None
            and loc["line_start"] <= evidence["line_end"]
            and evidence["line_start"] <= loc["line_end"]
            for loc in locators
        )
    raise ValueError(f"Unsupported evidence locator: {locator_type}")


def map_evidence(question: dict, chunks_by_version: dict) -> dict[str, str]:
    relevant = {}
    for evidence in question["evidence"]:
        version_id = evidence["version_id"]
        quote = normalize_quote(evidence["quote"])
        matches = [
            chunk.id for chunk, content in chunks_by_version[version_id]
            if quote
            and evidence["source_path"] == chunk.document_version.source_path
            and evidence["source_sha256"] == chunk.document_version.sha256
            and evidence["source_sha256"] == chunk.chunk_metadata["source_sha256"]
            and matches_locator(evidence, chunk.chunk_metadata)
            and quote in content
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{question['question_id']}: evidence in {version_id} "
                f"matched {len(matches)} chunks"
            )
        relevant[matches[0]] = version_id
    return relevant


def evaluate_retrieval(db: Session, dataset: Path, rank: Ranker) -> dict:
    raw = dataset.read_bytes()
    source = raw.decode("utf-8-sig")
    if dataset.suffix.lower() == ".json":
        questions = json.loads(source)
    else:
        questions = [json.loads(line) for line in source.splitlines() if line.strip()]
    if not isinstance(questions, list) or not all(isinstance(q, dict) for q in questions):
        raise ValueError("Evaluation dataset must contain a list of question objects")
    if not questions or len({q["question_id"] for q in questions}) != len(questions):
        raise ValueError("Evaluation needs nonempty, unique questions")
    if not any(q["expected_behavior"] in SCORED_BEHAVIORS for q in questions):
        raise ValueError("Evaluation needs at least one scored question")

    chunks = db.scalars(
        select(DocumentChunk)
        .options(joinedload(DocumentChunk.document_version))
        .order_by(DocumentChunk.id)
    ).all()
    chunks_by_id = {chunk.id: chunk for chunk in chunks}
    chunks_by_version = defaultdict(list)
    corpus_hash = hashlib.sha256()
    for chunk in chunks:
        content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        if content_hash != chunk.content_sha256:
            raise ValueError(f"Invalid content hash for {chunk.id}")
        corpus_hash.update(f"{chunk.id} {chunk.version_id} {content_hash}\n".encode())
        chunks_by_version[chunk.version_id].append(
            (chunk, normalize_quote(chunk.content))
        )

    rows = []
    for question in questions:
        question_id = question["question_id"]
        behavior = question["expected_behavior"]
        document_id = None
        if question["query_mode"] == "document":
            document_id = question["access_target_document_id"]
            if not document_id:
                raise ValueError(f"{question_id}: missing explicit document")
        elif question["query_mode"] != "general":
            raise ValueError(f"{question_id}: unknown query mode")

        scope = resolve_scope(
            db, question["user_id"], date.fromisoformat(question["as_of_date"]),
            document_id,
        )
        expected_status = "access_denied" if behavior == "access_denied" else "ok"
        if scope.status != expected_status:
            raise ValueError(f"{question_id}: unexpected retrieval scope status")
        relevant = map_evidence(question, chunks_by_version)
        if not set(relevant.values()) <= scope.version_ids:
            raise ValueError(f"{question_id}: labeled evidence outside candidate scope")
        if behavior in SCORED_BEHAVIORS and not relevant:
            raise ValueError(f"{question_id}: missing evidence labels")
        if behavior == "report_conflict" and len(set(relevant.values())) < 2:
            raise ValueError(f"{question_id}: missing conflicting versions")

        # On evidence-insufficient cases, named targets denote unavailable evidence.
        forbidden_versions = set(question["target_version_ids"]) if (
            behavior == "insufficient_evidence" and question["forbidden_disclosure"]
        ) else set()
        if forbidden_versions & scope.version_ids:
            raise ValueError(f"{question_id}: unavailable evidence in candidate scope")

        hits = rank(question["question"], scope, max(TOP_K))
        hit_ids = [hit.chunk_id for hit in hits]
        if len(hits) > max(TOP_K) or len(set(hit_ids)) != len(hits):
            raise ValueError(f"{question_id}: invalid ranking length or duplicate hit")
        for hit in hits:
            chunk = chunks_by_id.get(hit.chunk_id)
            if (
                chunk is None
                or chunk.version_id != hit.version_id
                or hit.version_id not in scope.version_ids
                or not math.isfinite(hit.score)
            ):
                raise ValueError(f"{question_id}: invalid or out-of-scope hit")

        metrics = {}
        for k in TOP_K:
            retrieved = hit_ids[:k]
            matched = set(retrieved) & relevant.keys()
            rank_of_first = next(
                (index for index, chunk_id in enumerate(retrieved, 1)
                 if chunk_id in relevant),
                None,
            )
            if relevant:
                metrics[k] = {
                    "recall": len(matched) / len(relevant),
                    "reciprocal_rank": 1 / rank_of_first if rank_of_first else 0.0,
                    "missing_chunk_ids": sorted(relevant.keys() - matched),
                }
            if behavior == "report_conflict":
                metrics[k]["all_conflicting_versions_hit"] = (
                    {relevant[chunk_id] for chunk_id in matched} == set(relevant.values())
                )

        rows.append({
            "question_id": question_id,
            "expected_behavior": behavior,
            "status": scope.status,
            "eligible_version_ids": sorted(scope.version_ids),
            "relevant_chunks": relevant,
            "hits": [
                {"chunk_id": h.chunk_id, "version_id": h.version_id, "score": h.score}
                for h in hits
            ],
            "at_k": metrics,
        })

    scored = [row for row in rows if row["expected_behavior"] in SCORED_BEHAVIORS]
    conflicts = [row for row in scored if row["expected_behavior"] == "report_conflict"]
    return {
        "dataset_name": dataset.name,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "corpus_sha256": corpus_hash.hexdigest(),
        "question_count": len(rows),
        "chunk_count": len(chunks),
        "evidence_count": sum(len(q["evidence"]) for q in questions),
        "scored_count": len(scored),
        "conflict_count": len(conflicts),
        "access_denied_count": sum(row["status"] == "access_denied" for row in rows),
        "summary": {
            k: {
                "recall": mean(row["at_k"][k]["recall"] for row in scored),
                "mrr": mean(row["at_k"][k]["reciprocal_rank"] for row in scored),
                "all_conflicting_versions_hit": sum(
                    row["at_k"][k]["all_conflicting_versions_hit"] for row in conflicts
                ),
            }
            for k in TOP_K
        },
        "questions": rows,
    }
