"""In-memory conversation histories that expire after a period without activity."""

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.domain.models import ChatMessage


@dataclass(frozen=True, slots=True)
class _Entry:
    messages: tuple[ChatMessage, ...]
    expires_at: float


class ConversationStore:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        # FastAPI runs sync endpoints in a thread pool.
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def history(self, conversation_id: str) -> tuple[ChatMessage, ...]:
        with self._lock:
            entry = self._entries.get(conversation_id)
            if entry is None or entry.expires_at <= self._clock():
                return ()
            return entry.messages

    def save(self, conversation_id: str, messages: Sequence[ChatMessage]) -> None:
        with self._lock:
            now = self._clock()
            self._entries = {
                key: entry for key, entry in self._entries.items() if entry.expires_at > now
            }
            self._entries[conversation_id] = _Entry(tuple(messages), now + self._ttl)
