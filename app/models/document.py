from datetime import date

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.identity import Department, Role


document_roles = Table(
    "document_roles",
    Base.metadata,
    Column(
        "document_id",
        String(32),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

document_departments = Table(
    "document_departments",
    Base.metadata,
    Column(
        "document_id",
        String(32),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "department_id",
        ForeignKey("departments.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Document(Base):
    __tablename__ = "documents"

    __table_args__ = (
        CheckConstraint(
            "access_policy = 'role_or_department'",
            name="ck_documents_access_policy",
        ),
        CheckConstraint(
            "acl_revision >= 1",
            name="ck_documents_acl_revision",
        ),
    )

    # 直接使用清单中的 document_id，例如 FIN-001。
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), index=True)

    access_policy: Mapped[str] = mapped_column(
        String(32),
        default="role_or_department",
    )
    acl_revision: Mapped[int] = mapped_column(default=1)
    source_scope: Mapped[str] = mapped_column(String(100))
    acl_basis: Mapped[dict] = mapped_column(JSON)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
    )

    allowed_roles: Mapped[list[Role]] = relationship(
        secondary=document_roles,
    )
    allowed_departments: Mapped[list[Department]] = relationship(
        secondary=document_departments,
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version",
            name="uq_document_versions_document_version",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from < effective_to",
            name="ck_document_versions_effective_range",
        ),
        CheckConstraint(
            "source_format IN ('pdf', 'docx', 'md')",
            name="ck_document_versions_source_format",
        ),
        CheckConstraint(
            "source_status IN ('current', 'archived')",
            name="ck_document_versions_source_status",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_document_versions_size",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_document_versions_page_count",
        ),
    )

    # 直接使用清单中的 version_id，例如 FIN-001-v2。
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"),
        index=True,
    )

    version: Mapped[str] = mapped_column(String(32))
    source_path: Mapped[str] = mapped_column(String(1024))
    source_format: Mapped[str] = mapped_column(String(10))
    source_status: Mapped[str] = mapped_column(String(20))

    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[date | None] = mapped_column(Date)

    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    page_count: Mapped[int | None]

    locator_type: Mapped[str] = mapped_column(String(64))
    preview_pdf_path: Mapped[str | None] = mapped_column(String(1024))
    preview_status: Mapped[str] = mapped_column(String(32))

    document: Mapped["Document"] = relationship(
        back_populates="versions",
    )