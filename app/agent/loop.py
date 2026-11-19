"""Tool-calling loop: asks the model, runs the requested tools and repeats until it answers."""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from app.agent import audit
from app.agent.drafts import DEFAULT_DRAFT_TTL_SECONDS, DraftStore
from app.agent.prompts import system_prompt
from app.agent.tools import AGENT_TOOLS, Tool, ToolContext, run_tool, tool_specs
from app.domain.models import UNKNOWN_USAGE, ChatMessage, QuotationDraft, ToolCall, Usage
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
    # Last quotation draft prepared in this turn, waiting for the user's confirmation.
    pending_draft: QuotationDraft | None = None
    # Tokens and estimated cost of every model call in the turn.
    usage: Usage = Usage()


@dataclass(slots=True)
class _Progress:
    conversation: list[ChatMessage]
    calls: list[ToolCall] = field(default_factory=list)
    draft: QuotationDraft | None = None
    usage: Usage = field(default_factory=Usage)

    def finish(self, answer: str, reason: StopReason) -> AgentTurn:
        return AgentTurn(
            answer, reason, tuple(self.calls), tuple(self.conversation), self.draft, self.usage
        )


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
        drafts: DraftStore | None = None,
        tools: Mapping[str, Tool] = AGENT_TOOLS,
    ) -> None:
        self._llm = llm
        self._gateway = gateway
        self._max_iterations = max_iterations
        self._timeout = timeout_seconds
        self._clock = clock
        self._today = today
        self._drafts = drafts or DraftStore(ttl_seconds=DEFAULT_DRAFT_TTL_SECONDS)
        self._tools = tools
        self._specs = tool_specs(tools)

    def run(
        self, history: Sequence[ChatMessage], message: str, conversation_id: str = ""
    ) -> AgentTurn:
        deadline = self._clock() + self._timeout
        today = self._today()
        context = ToolContext(
            gateway=self._gateway,
            today=today,
            drafts=self._drafts,
            conversation_id=conversation_id,
        )
        system = ChatMessage(role="system", content=system_prompt(today))
        progress = _Progress(conversation=[*history, ChatMessage(role="user", content=message)])

        turn = self._loop(system, context, progress, deadline)
        logger.info(
            "Agent turn: conversation_id=%s stop=%s input_tokens=%d output_tokens=%d cost_usd=%s",
            conversation_id,
            turn.stop_reason,
            turn.usage.input_tokens,
            turn.usage.output_tokens,
            turn.usage.cost_usd,
        )
        return turn

    def _loop(
        self, system: ChatMessage, context: ToolContext, progress: _Progress, deadline: float
    ) -> AgentTurn:
        for _ in range(self._max_iterations):
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._stop("timeout", progress)
            reply = self._llm.complete([system, *progress.conversation], self._specs, remaining)
            progress.usage += reply.usage or UNKNOWN_USAGE
            progress.conversation.append(reply)
            if not reply.tool_calls:
                return progress.finish(reply.content, "answered")
            for call in reply.tool_calls:
                progress.calls.append(call)
                progress.conversation.append(self._run_tool(call, context, progress))
        return self._stop("max_iterations", progress)

    def _run_tool(self, call: ToolCall, context: ToolContext, progress: _Progress) -> ChatMessage:
        started = self._clock()
        result = run_tool(self._tools, call, context)
        audit.tool_call(
            conversation_id=context.conversation_id,
            tool=call.name,
            arguments=call.arguments,
            outcome="error" if result.is_error else "ok",
            duration=self._clock() - started,
        )
        if result.draft is not None:
            progress.draft = result.draft
        return ChatMessage(
            role="tool", content=result.content, tool_call_id=call.id, tool_name=call.name
        )

    @staticmethod
    def _stop(reason: StopReason, progress: _Progress) -> AgentTurn:
        logger.warning("Agent stopped: %s", reason)
        answer = _STOP_MESSAGES[reason]
        # Closing with an assistant message keeps the history valid for the next turn.
        progress.conversation.append(ChatMessage(role="assistant", content=answer))
        return progress.finish(answer, reason)
