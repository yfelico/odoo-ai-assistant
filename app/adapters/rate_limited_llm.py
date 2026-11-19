"""LLMClient decorator that keeps requests under a per-minute quota (e.g. free API tiers)."""

import time
from collections import deque
from collections.abc import Callable, Sequence

from app.domain.models import ChatMessage, ToolSpec
from app.domain.ports import LLMClient

WINDOW_SECONDS = 60.0


class RateLimitedLLM:
    """Waits before a request that would exceed ``requests_per_minute`` in a sliding minute.

    ``waited_seconds`` accumulates the time spent waiting, so callers can leave it out of
    latency measurements and time budgets.
    """

    def __init__(
        self,
        inner: LLMClient,
        *,
        requests_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        self._inner = inner
        self._limit = requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self.waited_seconds = 0.0

    def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], timeout: float
    ) -> ChatMessage:
        self._wait_for_quota()
        self._sent.append(self._clock())
        return self._inner.complete(messages, tools, timeout)

    def _wait_for_quota(self) -> None:
        now = self._clock()
        while self._sent and now - self._sent[0] >= WINDOW_SECONDS:
            self._sent.popleft()
        if len(self._sent) < self._limit:
            return
        wait = WINDOW_SECONDS - (now - self._sent[0])
        self._sleep(wait)
        self.waited_seconds += wait
        self._sent.popleft()
