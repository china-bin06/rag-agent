from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.document import DocumentVersion


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "chunk_index",
            name="uq_document_chunks_version_index",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index",
        ),
        CheckConstraint(
            "chunk_type IN ('clause_group', 'table', 'appendix')",
            name="ck_document_chunks_type",
        ),
        CheckConstraint(
            "length(content) > 0",
            name="ck_document_chunks_content",
        ),
    )

    # 对应切片文件中的metadata.chunk_id。
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"),
        index=True,
    )
    chunk_index: Mapped[int]
    chunk_type: Mapped[str] = mapped_column(String(32))

    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))

    # metadata是SQLAlchemy保留属性名，因此使用chunk_metadata。
    chunk_metadata: Mapped[dict] = mapped_column(JSON)

    # 后续建立向量索引时填写。
    vector_record_id: Mapped[str | None] = mapped_column(String(128))

    document_version: Mapped[DocumentVersion] = relationship()