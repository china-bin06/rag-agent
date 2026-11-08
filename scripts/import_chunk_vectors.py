import hashlib
from uuid import NAMESPACE_URL, uuid4, uuid5

import numpy as np
from qdrant_client import QdrantClient, models
from sqlalchemy import select

from app.db.database import SessionLocal
from app.models import DocumentChunk, DocumentVersion
from app.rag.embeddings import DIMENSION, embedding_config, encode_texts
from app.rag.vector import COLLECTION_ALIAS, QDRANT_PATH


BATCH_SIZE = 64


def read_chunks() -> list[dict]:
    with SessionLocal() as db:
        records = db.execute(
            select(DocumentChunk, DocumentVersion)
            .join(DocumentVersion)
            .order_by(DocumentChunk.id)
        ).all()

        rows = []
        for chunk, version in records:
            digest = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
            if digest != chunk.content_sha256:
                raise ValueError(f"切片正文哈希异常：{chunk.id}")
            if (
                chunk.chunk_metadata["source_sha256"] != version.sha256
                or chunk.chunk_metadata["document_id"] != version.document_id
            ):
                raise ValueError(f"切片来源异常：{chunk.id}")
            rows.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": version.document_id,
                    "version_id": version.id,
                    "content_sha256": digest,
                    "content": chunk.content,
                    "metadata": chunk.chunk_metadata,
                }
            )

    if not rows:
        raise ValueError("数据库中没有可编码的切片")
    return rows


def import_vectors(client: QdrantClient) -> None:
    config = embedding_config()
    rows = read_chunks()
    vectors = encode_texts([row["content"] for row in rows], show_progress=True)
    collection = f"{COLLECTION_ALIAS}_{uuid4().hex}"
    previous = next(
        (
            alias.collection_name
            for alias in client.get_aliases().aliases
            if alias.alias_name == COLLECTION_ALIAS
        ),
        None,
    )
    client.create_collection(
        collection,
        vectors_config=models.VectorParams(size=DIMENSION, distance=models.Distance.COSINE),
        metadata={"embedding": config},
    )

    try:
        for start in range(0, len(rows), BATCH_SIZE):
            end = start + BATCH_SIZE
            points = [
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"document_chunks/{row['chunk_id']}")),
                    vector=vector.tolist(),
                    payload=row,
                )
                for row, vector in zip(rows[start:end], vectors[start:end], strict=True)
            ]
            client.upsert(collection, points, wait=True)
            saved = client.retrieve(collection, [point.id for point in points], with_vectors=True)
            saved_by_id = {record.id: record for record in saved}
            for point in points:
                record = saved_by_id.get(point.id)
                if (
                    record is None
                    or record.payload != point.payload
                    or not isinstance(record.vector, list)
                    or len(record.vector) != DIMENSION
                    or not np.allclose(record.vector, point.vector, rtol=0, atol=1e-6)
                ):
                    raise ValueError(f"向量库写入校验失败：{point.payload['chunk_id']}")

        if client.count(collection, exact=True).count != len(rows):
            raise ValueError("向量库记录数与源切片数不一致")
        if read_chunks() != rows:
            raise ValueError("入库期间源切片发生变化，请重新运行")
    except Exception:
        client.delete_collection(collection)
        raise

    # 整批校验通过后，再把固定别名切换到新集合。
    operations = []
    if previous is not None:
        operations.append(
            models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=COLLECTION_ALIAS)
            )
        )
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(
                alias_name=COLLECTION_ALIAS,
                collection_name=collection,
            )
        )
    )
    client.update_collection_aliases(operations)

    if previous is not None and previous.startswith(f"{COLLECTION_ALIAS}_"):
        client.delete_collection(previous)

    print(f"向量入库完成：{len(rows)} 条，{DIMENSION} 维，集合别名 {COLLECTION_ALIAS}")


def main() -> None:
    client = QdrantClient(path=str(QDRANT_PATH))
    try:
        import_vectors(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
