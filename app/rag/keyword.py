import math
import re
import unicodedata
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk
from app.rag.retrieval import RetrievalHit, RetrievalScope


HAN = r"[\u3400-\u4dbf\u4e00-\u9fff]"
TOKEN_PATTERN = re.compile(rf"{HAN}+|[a-z0-9]+(?:[-.,][a-z0-9]+)*")
K1 = 1.2
B = 0.75


def tokenize(text: str) -> list[str]:
    terms = []
    for token in TOKEN_PATTERN.findall(unicodedata.normalize("NFKC", text).lower()):
        if len(token) > 1 and re.fullmatch(HAN, token[0]):
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return terms


class KeywordRanker:
    def __init__(self, db: Session):
        self.db = db

    def __call__(
        self, query: str, scope: RetrievalScope, limit: int
    ) -> list[RetrievalHit]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_terms = set(tokenize(query))
        if scope.status != "ok" or not scope.version_ids or not query_terms:
            return []

        chunks = self.db.scalars(
            select(DocumentChunk).where(
                DocumentChunk.version_id.in_(scope.version_ids)
            )
        ).all()
        token_counts = [Counter(tokenize(chunk.content)) for chunk in chunks]
        lengths = [sum(counts.values()) for counts in token_counts]
        total_length = sum(lengths)
        if not total_length:
            return []

        avg_length = total_length / len(chunks)
        document_frequency = Counter()
        for counts in token_counts:
            document_frequency.update(query_terms.intersection(counts))
        idf = {
            term: math.log1p((len(chunks) - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

        hits = []
        for chunk, counts, length in zip(chunks, token_counts, lengths, strict=True):
            norm = K1 * (1 - B + B * length / avg_length)
            # Stable addition order also keeps equal-score tie handling reproducible.
            score = math.fsum(
                idf[term] * counts[term] * (K1 + 1) / (counts[term] + norm)
                for term in sorted(query_terms.intersection(counts))
            )
            if score > 0:
                hits.append(
                    RetrievalHit(
                        chunk.id, chunk.version_id, score, chunk.chunk_metadata
                    )
                )

        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))[:limit]
