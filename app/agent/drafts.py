"""In-memory quotation drafts that wait for the user's confirmation and then expire."""

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.domain.models import QuotationDraft, QuotationLine

DEFAULT_DRAFT_TTL_SECONDS = 900.0


class DraftNotFoundError(Exception):
    """The draft does not exist, has expired or was already confirmed."""


@dataclass(frozen=True, slots=True)
class _Entry:
    draft: QuotationDraft
    expires_at: float


class DraftStore:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        # FastAPI runs sync endpoints in a thread pool.
        self._lock = threading.Lock()

    def create(
        self, *, partner_id: int, partner_name: str, lines: Sequence[QuotationLine]
    ) -> QuotationDraft:
        draft = QuotationDraft(
            id=uuid.uuid4().hex,
            partner_id=partner_id,
            partner_name=partner_name,
            lines=tuple(lines),
            estimated_total_untaxed=round(sum(line.subtotal for line in lines), 2),
        )
        self._put(draft)
        return draft

    def get(self, draft_id: str) -> QuotationDraft:
        with self._lock:
            return self._live(draft_id).draft

    def take(self, draft_id: str) -> QuotationDraft:
        """Remove and return the draft, so it can only be confirmed once."""
        with self._lock:
            entry = self._live(draft_id)
            del self._entries[draft_id]
            return entry.draft

    def restore(self, draft: QuotationDraft) -> None:
        """Make a taken draft available again, e.g. after the ERP failed to create it."""
        self._put(draft)

    def _put(self, draft: QuotationDraft) -> None:
        with self._lock:
            now = self._clock()
            self._entries = {
                key: entry for key, entry in self._entries.items() if entry.expires_at > now
            }
            self._entries[draft.id] = _Entry(draft, now + self._ttl)

    def _live(self, draft_id: str) -> _Entry:
        entry = self._entries.get(draft_id)
        if entry is None or entry.expires_at <= self._clock():
            raise DraftNotFoundError("The draft does not exist or has expired")
        return entry
