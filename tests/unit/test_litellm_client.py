from collections.abc import Mapping

import litellm
import pytest
from pydantic import SecretStr

from app.adapters.litellm_client import LiteLLMClient
from app.config import Settings
from app.domain.models import ChatMessage, LLMError, ToolCall, ToolSpec

MODEL = "gemini/test-model"
SPEC = ToolSpec(
    name="search_partners",
    description="Find customers.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}},
)
GEMINI_TOOL_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "search_partners", "arguments": '{"name": "acme"}'},
    # Gemini needs its thought signature echoed back on the next request.
    "provider_specific_fields": {"thought_signature": "c2lnbmF0dXJl"},
}


class FakeCompletion:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(message: Mapping[str, object]) -> dict[str, object]:
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": dict(message)}]}


def _client(completion: FakeCompletion, **options: object) -> LiteLLMClient:
    return LiteLLMClient(MODEL, completion=completion, **options)  # type: ignore[arg-type]


def test_sends_messages_and_tools_in_openai_format() -> None:
    completion = FakeCompletion(_response({"role": "assistant", "content": "Hola"}))

    _client(completion).complete(
        [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hola")],
        [SPEC],
        timeout=42.0,
    )

    assert completion.calls == [
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hola"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "search_partners",
                        "description": "Find customers.",
                        "parameters": SPEC.parameters,
                    },
                }
            ],
            "timeout": 42.0,
        }
    ]


def test_sends_api_key_and_reasoning_effort_when_configured() -> None:
    completion = FakeCompletion(_response({"role": "assistant", "content": "Hola"}))

    _client(completion, api_key="secret", reasoning_effort="low").complete(
        [ChatMessage(role="user", content="hola")], [], timeout=5.0
    )

    call = completion.calls[0]
    assert call["api_key"] == "secret"
    assert call["reasoning_effort"] == "low"
    assert "tools" not in call
    assert "num_retries" not in call


def test_asks_litellm_to_retry_transient_provider_errors() -> None:
    completion = FakeCompletion(_response({"role": "assistant", "content": "Hola"}))

    _client(completion, num_retries=3).complete(
        [ChatMessage(role="user", content="hola")], [], timeout=5.0
    )

    assert completion.calls[0]["num_retries"] == 3


def test_parses_a_text_reply() -> None:
    completion = FakeCompletion(_response({"role": "assistant", "content": "Hola"}))

    reply = _client(completion).complete([ChatMessage(role="user", content="hola")], [], 5.0)

    assert (reply.role, reply.content, reply.tool_calls) == ("assistant", "Hola", ())


def test_parses_tool_calls() -> None:
    completion = FakeCompletion(
        _response({"role": "assistant", "content": None, "tool_calls": [GEMINI_TOOL_CALL]})
    )

    reply = _client(completion).complete([ChatMessage(role="user", content="acme")], [SPEC], 5.0)

    assert reply.content == ""
    assert reply.tool_calls == (
        ToolCall(id="call_1", name="search_partners", arguments='{"name": "acme"}'),
    )


def test_resends_the_provider_message_unchanged() -> None:
    raw = {"role": "assistant", "content": None, "tool_calls": [GEMINI_TOOL_CALL]}
    completion = FakeCompletion(
        _response({**raw, "function_call": None}),
        _response({"role": "assistant", "content": "Listo"}),
    )
    client = _client(completion)
    user = ChatMessage(role="user", content="acme")

    reply = client.complete([user], [SPEC], 5.0)
    tool = ChatMessage(
        role="tool", content="<odoo_data>[]</odoo_data>", tool_call_id="call_1", tool_name="x"
    )
    client.complete([user, reply, tool], [SPEC], 5.0)

    assert completion.calls[1]["messages"] == [
        {"role": "user", "content": "acme"},
        raw,
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "x",
            "content": "<odoo_data>[]</odoo_data>",
        },
    ]


def test_builds_assistant_messages_without_provider_payload() -> None:
    completion = FakeCompletion(_response({"role": "assistant", "content": "ok"}))
    call = ToolCall(id="c1", name="search_partners", arguments="{}")

    _client(completion).complete(
        [ChatMessage(role="assistant", content="", tool_calls=(call,))], [], 5.0
    )

    assert completion.calls[0]["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "search_partners", "arguments": "{}"},
                }
            ],
        }
    ]


def test_provider_errors_become_llm_errors() -> None:
    error = litellm.exceptions.RateLimitError(
        message="quota exceeded", llm_provider="gemini", model=MODEL
    )
    completion = FakeCompletion(error)

    with pytest.raises(LLMError):
        _client(completion).complete([ChatMessage(role="user", content="hola")], [], 5.0)


def test_malformed_responses_become_llm_errors() -> None:
    completion = FakeCompletion({"choices": []})

    with pytest.raises(LLMError):
        _client(completion).complete([ChatMessage(role="user", content="hola")], [], 5.0)


def test_from_settings_uses_model_key_and_effort() -> None:
    settings = Settings(
        odoo_api_key=SecretStr("unused"),
        llm_model=MODEL,
        llm_api_key=SecretStr("secret"),
        llm_reasoning_effort="medium",
        llm_num_retries=2,
    )
    completion = FakeCompletion(_response({"role": "assistant", "content": "ok"}))

    client = LiteLLMClient.from_settings(settings, completion=completion)
    client.complete([ChatMessage(role="user", content="hola")], [], 5.0)

    call = completion.calls[0]
    assert (call["model"], call["api_key"], call["reasoning_effort"], call["num_retries"]) == (
        MODEL,
        "secret",
        "medium",
        2,
    )
