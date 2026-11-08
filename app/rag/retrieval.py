from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import can_read_document
from app.models import Document, DocumentVersion, User


@dataclass(frozen=True)
class RetrievalScope:
    version_ids: frozenset[str]
    status: Literal["ok", "access_denied"] = "ok"


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    version_id: str
    score: float
    metadata: dict


Ranker = Callable[[str, RetrievalScope, int], list[RetrievalHit]]


def resolve_scope(
    db: Session,
    user_id: str,
    as_of_date: date,
    document_id: str | None = None,
) -> RetrievalScope:
    """Resolve the candidate versions using the trusted DB identity and ACL."""
    user = db.get(
        User,
        user_id,
        options=(selectinload(User.department), selectinload(User.roles)),
    )
    if user is None or not user.is_active:
        return RetrievalScope(frozenset(), "access_denied")

    documents = select(Document).options(
        selectinload(Document.allowed_roles),
        selectinload(Document.allowed_departments),
    )
    if document_id is not None:
        documents = documents.where(Document.id == document_id)

    readable_ids = [
        document.id for document in db.scalars(documents)
        if can_read_document(user, document)
    ]
    if not readable_ids:
        status = "access_denied" if document_id is not None else "ok"
        return RetrievalScope(frozenset(), status)

    versions = db.scalars(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id.in_(readable_ids),
            DocumentVersion.effective_from <= as_of_date,
            or_(
                DocumentVersion.effective_to.is_(None),
                DocumentVersion.effective_to > as_of_date,
            ),
        )
    )
    return RetrievalScope(frozenset(versions))
