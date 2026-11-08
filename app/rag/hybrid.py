from collections import defaultdict
from dataclasses import replace

from app.rag.retrieval import Ranker, RetrievalHit, RetrievalScope


BRANCH_DEPTH = 20
RRF_K = 60


class HybridRanker:
    def __init__(self, keyword: Ranker, vector: Ranker):
        self.keyword = keyword
        self.vector = vector

    def __call__(
        self, query: str, scope: RetrievalScope, limit: int
    ) -> list[RetrievalHit]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if scope.status != "ok" or not scope.version_ids or not query.strip():
            return []

        hits_by_id: dict[str, RetrievalHit] = {}
        scores: dict[str, float] = defaultdict(float)
        for ranker in (self.keyword, self.vector):
            for rank, hit in enumerate(ranker(query, scope, BRANCH_DEPTH), 1):
                hits_by_id.setdefault(hit.chunk_id, hit)
                scores[hit.chunk_id] += 1 / (RRF_K + rank)

        ranked_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
        return [
            replace(hits_by_id[chunk_id], score=scores[chunk_id])
            for chunk_id in ranked_ids[:limit]
        ]
