from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from threading import Lock
from time import monotonic
from uuid import UUID


CLARIFICATION_TTL_SECONDS = 15 * 60
MAX_UNCLEAR_REPLIES = 2
SessionKey = tuple[str, UUID]


@dataclass(frozen=True)
class PendingClarification:
    question: str
    rule: str
    as_of_date: date
    document_id: str | None
    expires_at: float
    unclear_replies: int = 0


class SessionBusyError(RuntimeError):
    pass


class ClarificationStore:
    def __init__(self):
        self._pending: dict[SessionKey, PendingClarification] = {}
        self._active: set[SessionKey] = set()
        self._lock = Lock()

    @contextmanager
    def turn(self, key: SessionKey) -> Iterator[PendingClarification | None]:
        with self._lock:
            if key in self._active:
                raise SessionBusyError
            now = monotonic()
            self._pending = {
                k: state for k, state in self._pending.items() if state.expires_at > now
            }
            self._active.add(key)
            pending = self._pending.get(key)
        try:
            yield pending
        finally:
            with self._lock:
                self._active.remove(key)

    def save(self, key: SessionKey, state: PendingClarification) -> None:
        with self._lock:
            self._pending[key] = state

    def clear(self, key: SessionKey) -> None:
        with self._lock:
            self._pending.pop(key, None)
