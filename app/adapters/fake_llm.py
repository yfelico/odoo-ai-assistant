"""Scripted LLMClient for tests."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.domain.models import ChatMessage, LLMError, ToolSpec


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: list[ChatMessage]
    tools: list[ToolSpec]
    timeout: float


class FakeLLM:
    """Returns the scripted replies in order and records every request."""

    def __init__(self, replies: Iterable[ChatMessage]) -> None:
        self.replies = list(replies)
        self.requests: list[LLMRequest] = []
        self.on_request: Callable[[], None] | None = None

    def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], timeout: float
    ) -> ChatMessage:
        self.requests.append(LLMRequest(list(messages), list(tools), timeout))
        if self.on_request is not None:
            self.on_request()
        if not self.replies:
            raise LLMError("FakeLLM script exhausted")
        return self.replies.pop(0)
