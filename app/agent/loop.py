"""Tool-calling loop: asks the model, runs the requested tools and repeats until it answers."""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from app.agent.prompts import system_prompt
from app.agent.tools import READ_TOOLS, Tool, ToolContext, run_tool, tool_specs
from app.domain.models import ChatMessage, ToolCall
from app.domain.ports import ERPGateway, LLMClient

logger = logging.getLogger(__name__)

StopReason = Literal["answered", "max_iterations", "timeout"]

_STOP_MESSAGES: Mapping[StopReason, str] = {
    "max_iterations": (
        "No he podido completar la respuesta: la consulta necesitaba demasiados pasos. "
        "Prueba a dividirla en preguntas más concretas."
    ),
    "timeout": (
        "No he podido completar la respuesta a tiempo. Inténtalo de nuevo en unos segundos."
    ),
}


@dataclass(frozen=True, slots=True)
class AgentTurn:
    answer: str
    stop_reason: StopReason
    tool_calls: tuple[ToolCall, ...]
    # Conversation without the system prompt, ready to pass back on the next turn.
    messages: tuple[ChatMessage, ...]


def _utc_today() -> date:
    return datetime.now(UTC).date()


class Agent:
    # Collaborators and limits are injected so tests can control them.
    def __init__(  # noqa: PLR0913
        self,
        *,
        llm: LLMClient,
        gateway: ERPGateway,
        max_iterations: int,
        timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = _utc_today,
        tools: Mapping[str, Tool] = READ_TOOLS,
    ) -> None:
        self._llm = llm
        self._gateway = gateway
        self._max_iterations = max_iterations
        self._timeout = timeout_seconds
        self._clock = clock
        self._today = today
        self._tools = tools
        self._specs = tool_specs(tools)

    def run(self, history: Sequence[ChatMessage], message: str) -> AgentTurn:
        deadline = self._clock() + self._timeout
        today = self._today()
        context = ToolContext(gateway=self._gateway, today=today)
        system = ChatMessage(role="system", content=system_prompt(today))
        conversation = [*history, ChatMessage(role="user", content=message)]
        calls: list[ToolCall] = []

        for _ in range(self._max_iterations):
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._stop("timeout", conversation, calls)
            reply = self._llm.complete([system, *conversation], self._specs, remaining)
            conversation.append(reply)
            if not reply.tool_calls:
                return AgentTurn(reply.content, "answered", tuple(calls), tuple(conversation))
            for call in reply.tool_calls:
                calls.append(call)
                conversation.append(self._run_tool(call, context))
        return self._stop("max_iterations", conversation, calls)

    def _run_tool(self, call: ToolCall, context: ToolContext) -> ChatMessage:
        started = self._clock()
        result = run_tool(self._tools, call, context)
        logger.info(
            "Tool %s finished in %.3fs (error=%s)",
            call.name,
            self._clock() - started,
            result.is_error,
        )
        return ChatMessage(
            role="tool", content=result.content, tool_call_id=call.id, tool_name=call.name
        )

    @staticmethod
    def _stop(
        reason: StopReason, conversation: list[ChatMessage], calls: list[ToolCall]
    ) -> AgentTurn:
        logger.warning("Agent stopped: %s", reason)
        answer = _STOP_MESSAGES[reason]
        # Closing with an assistant message keeps the history valid for the next turn.
        conversation.append(ChatMessage(role="assistant", content=answer))
        return AgentTurn(answer, reason, tuple(calls), tuple(conversation))
