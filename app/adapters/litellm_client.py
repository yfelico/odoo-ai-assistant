"""LLMClient over LiteLLM, which speaks the OpenAI chat format to every provider."""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, Self, cast

import litellm
import openai
from pydantic import BaseModel

from app.config import Settings
from app.domain.models import ChatMessage, LLMError, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

type Completion = Callable[..., object]
type WireMessage = dict[str, object]

ROLE = "role"
CONTENT = "content"
TOOL_CALLS = "tool_calls"
FUNCTION = "function"


class LiteLLMClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        num_retries: int = 0,
        completion: Completion = litellm.completion,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._reasoning_effort = reasoning_effort
        self._num_retries = num_retries
        self._completion = completion

    @classmethod
    def from_settings(cls, settings: Settings, completion: Completion | None = None) -> Self:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        return cls(
            settings.llm_model,
            api_key=api_key or None,
            reasoning_effort=settings.llm_reasoning_effort,
            num_retries=settings.llm_num_retries,
            completion=completion or litellm.completion,
        )

    def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], timeout: float
    ) -> ChatMessage:
        request: dict[str, object] = {
            "model": self._model,
            "messages": [_to_wire(message) for message in messages],
        }
        if tools:
            request["tools"] = [_tool_to_wire(tool) for tool in tools]
        request["timeout"] = timeout
        if self._api_key:
            request["api_key"] = self._api_key
        if self._reasoning_effort:
            request["reasoning_effort"] = self._reasoning_effort
        if self._num_retries:
            request["num_retries"] = self._num_retries
        try:
            response = self._completion(**request)
        except openai.OpenAIError as err:
            # LiteLLM raises OpenAI-compatible exceptions for every provider.
            logger.warning("LLM request failed: %s", type(err).__name__)
            raise LLMError(f"The model provider failed ({type(err).__name__})") from err
        return _from_wire(_first_message(response))


def _to_wire(message: ChatMessage) -> WireMessage:
    if message.role == "assistant":
        if message.provider_payload is not None:
            return dict(message.provider_payload)
        return {
            ROLE: "assistant",
            CONTENT: message.content or None,
            **(
                {TOOL_CALLS: [_call_to_wire(c) for c in message.tool_calls]}
                if message.tool_calls
                else {}
            ),
        }
    if message.role == "tool":
        return {
            ROLE: "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.tool_name,
            CONTENT: message.content,
        }
    return {ROLE: message.role, CONTENT: message.content}


def _call_to_wire(call: ToolCall) -> WireMessage:
    return {
        "id": call.id,
        "type": FUNCTION,
        FUNCTION: {"name": call.name, "arguments": call.arguments},
    }


def _tool_to_wire(tool: ToolSpec) -> WireMessage:
    return {
        "type": FUNCTION,
        FUNCTION: {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        },
    }


def _first_message(response: object) -> WireMessage:
    data = response.model_dump() if isinstance(response, BaseModel) else response
    try:
        message = cast(Mapping[str, object], data)["choices"]
        return dict(cast(Sequence[Mapping[str, Mapping[str, object]]], message)[0]["message"])
    except (KeyError, IndexError, TypeError) as err:
        raise LLMError("The model returned an unexpected response") from err


def _from_wire(message: WireMessage) -> ChatMessage:
    # Kept verbatim (minus empty keys) so reasoning signatures survive the round trip.
    payload = {key: value for key, value in message.items() if value is not None or key == CONTENT}
    content = message.get(CONTENT)
    return ChatMessage(
        role="assistant",
        content=content if isinstance(content, str) else "",
        tool_calls=tuple(_call_from_wire(raw) for raw in _list(message.get(TOOL_CALLS))),
        provider_payload=payload,
    )


def _call_from_wire(raw: object) -> ToolCall:
    try:
        call = cast(Mapping[str, object], raw)
        function = cast(Mapping[str, object], call[FUNCTION])
        arguments = function.get("arguments")
        return ToolCall(
            id=str(call["id"]),
            name=str(function["name"]),
            arguments=arguments if isinstance(arguments, str) and arguments else "{}",
        )
    except (KeyError, TypeError) as err:
        raise LLMError("The model returned a malformed tool call") from err


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []
