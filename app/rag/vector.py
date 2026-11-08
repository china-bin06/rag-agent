from pathlib import Path

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk
from app.rag.embeddings import DIMENSION, embedding_config, encode_texts
from app.rag.retrieval import RetrievalHit, RetrievalScope


QDRANT_PATH = Path(__file__).resolve().parents[2] / "storage" / "qdrant"
COLLECTION_ALIAS = "document_chunks"


def validate_vector_index(client: QdrantClient, db: Session) -> None:
    """Check that dense and keyword retrieval rank the same corpus snapshot."""
    collection = client.get_collection(COLLECTION_ALIAS)
    config = collection.config
    vectors = config.params.vectors
    if (
        not isinstance(vectors, models.VectorParams)
        or vectors.size != DIMENSION
        or vectors.distance != models.Distance.COSINE
        or (config.metadata or {}).get("embedding") != embedding_config()
    ):
        raise ValueError("向量索引配置与本地编码器不一致，请重新导入切片向量")

    expected = {
        chunk_id: (version_id, content_hash)
        for chunk_id, version_id, content_hash in db.execute(
            select(DocumentChunk.id, DocumentChunk.version_id, DocumentChunk.content_sha256)
        )
    }
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION_ALIAS,
            offset=offset,
            limit=256,
            with_payload=["chunk_id", "version_id", "content_sha256"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            chunk_id = payload.get("chunk_id")
            signature = (payload.get("version_id"), payload.get("content_sha256"))
            if expected.pop(chunk_id, None) != signature:
                raise ValueError("向量索引包含重复、过期或额外切片，请重新导入切片向量")
        if offset is None:
            break
    if expected:
        raise ValueError("向量索引缺少 SQLite 切片，请重新导入切片向量")


class VectorRanker:
    def __init__(self, client: QdrantClient):
        self.client = client

    def __call__(
        self, query: str, scope: RetrievalScope, limit: int
    ) -> list[RetrievalHit]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if scope.status != "ok" or not scope.version_ids or not query.strip():
            return []

        vector = encode_texts([query])[0]
        response = self.client.query_points(
            COLLECTION_ALIAS,
            query=vector.tolist(),
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="version_id",
                        match=models.MatchAny(any=sorted(scope.version_ids)),
                    )
                ]
            ),
            limit=limit,
            with_payload=["chunk_id", "version_id", "metadata"],
            with_vectors=False,
        )

        hits = []
        for point in response.points:
            payload = point.payload or {}
            if payload.get("version_id") not in scope.version_ids:
                raise ValueError("向量检索返回了候选范围之外的切片")
            hits.append(
                RetrievalHit(
                    payload["chunk_id"], payload["version_id"],
                    point.score, payload["metadata"],
                )
            )
        return hits
