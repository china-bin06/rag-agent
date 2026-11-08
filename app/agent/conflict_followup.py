import re
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk
from app.rag.retrieval import RetrievalHit


COMPARISON = re.compile(r"到底|究竟|还是|冲突|不一致|以哪|哪[个条项份]为准|优先适用")
RELATION = re.compile(r"优先适用|继续保留|同时适用|相互补充|废止|替代")
SPECIAL_POLICIES = re.compile(r"([\u3400-\u9fff、]{2,36})专项制度")
MAX_RELATION_CHUNKS = 3


@dataclass(frozen=True)
class Policy:
    document_id: str
    title: str


def relation_pairs(
    version_id: str, content: str, policies: dict[str, Policy],
) -> set[frozenset[str]]:
    if version_id not in policies:
        return set()
    # Chunk headers identify the source, but do not assert a legal relationship.
    body = content.partition("\n\n")[2] if content.startswith("制度：") and "\n\n" in content else content
    pairs = set()
    for sentence in re.split(r"[。！？；\n]", body):
        if not RELATION.search(sentence):
            continue
        topics = {
            re.sub(r"^(?:公司)?(?:既有|原有|现行|相关)?", "", part)
            for group in SPECIAL_POLICIES.findall(sentence)
            for part in re.split(r"[、和及与或]", group)
        }
        for other_id, other in policies.items():
            if other.document_id == policies[version_id].document_id:
                continue
            if (
                other.document_id in sentence or other.title in sentence
                or any(len(topic) >= 2 and topic in other.title for topic in topics)
            ):
                pairs.add(frozenset((version_id, other_id)))
    return pairs


def find_missing_relations(
    db: Session,
    policies: dict[str, Policy],
    context: list[tuple[str, str]],
    retrieved_ids: set[str],
) -> tuple[str, list[RetrievalHit]]:
    missing = {
        frozenset((left, right)) for left, right in combinations(policies, 2)
        if policies[left].document_id != policies[right].document_id
    }
    for version_id, content in context:
        missing -= relation_pairs(version_id, content, policies)
    if not missing:
        return "no_missing_relation", []

    results = []
    # Only read the versions already cited and allowed by the request's scope.
    for chunk in db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.version_id.in_(policies))
        .order_by(DocumentChunk.version_id, DocumentChunk.chunk_index)
    ):
        if chunk.id in retrieved_ids:
            continue
        covered = relation_pairs(chunk.version_id, chunk.content, policies) & missing
        if covered:
            results.append(RetrievalHit(chunk.id, chunk.version_id, 0.0, chunk.chunk_metadata))
            missing -= covered
            if not missing or len(results) == MAX_RELATION_CHUNKS:
                break
    return ("found_relation" if results else "no_relation_found"), results
