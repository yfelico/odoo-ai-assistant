import json
from datetime import date

import pytest

from app.adapters.fake_erp import FakeERP
from app.adapters.fake_llm import FakeLLM
from app.agent.loop import Agent
from app.agent.tools import READ_TOOLS
from app.domain.models import ChatMessage, Partner, ToolCall

TODAY = date(2026, 11, 18)
TIMEOUT = 60.0
MAX_ITERATIONS = 3
ACME = Partner(id=1, name="Acme Corp", email=None, city="Springfield")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _call(call_id: str, name: str, /, **arguments: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def _assistant(content: str = "", *calls: ToolCall) -> ChatMessage:
    return ChatMessage(role="assistant", content=content, tool_calls=calls)


def _agent(llm: FakeLLM, clock: FakeClock | None = None) -> Agent:
    return Agent(
        llm=llm,
        gateway=FakeERP(partners=[ACME], orders=[]),
        max_iterations=MAX_ITERATIONS,
        timeout_seconds=TIMEOUT,
        clock=clock or FakeClock(),
        today=lambda: TODAY,
    )


def test_answers_directly_without_tools() -> None:
    llm = FakeLLM([_assistant("Hola")])

    turn = _agent(llm).run(history=(), message="hola")

    assert turn.answer == "Hola"
    assert turn.stop_reason == "answered"
    assert turn.tool_calls == ()
    sent = llm.requests[0].messages
    assert sent[0].role == "system"
    assert sent[-1] == ChatMessage(role="user", content="hola")


def test_offers_only_the_read_tools() -> None:
    llm = FakeLLM([_assistant("Hola")])

    _agent(llm).run(history=(), message="hola")

    assert {spec.name for spec in llm.requests[0].tools} == set(READ_TOOLS)


def test_system_prompt_has_today_and_the_injection_rule() -> None:
    llm = FakeLLM([_assistant("Hola")])

    _agent(llm).run(history=(), message="hola")

    system = llm.requests[0].messages[0].content
    assert "2026-11-18" in system
    assert "<odoo_data>" in system
    assert "do not translate" in system


def test_runs_a_tool_and_feeds_the_result_back() -> None:
    call = _call("c1", "search_partners", name="acme")
    llm = FakeLLM([_assistant("", call), _assistant("Acme Corp está en Springfield.")])

    turn = _agent(llm).run(history=(), message="¿Dónde está Acme?")

    assert turn.answer == "Acme Corp está en Springfield."
    assert turn.tool_calls == (call,)
    tool_message = llm.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "c1"
    assert tool_message.tool_name == "search_partners"
    assert "<odoo_data>" in tool_message.content
    assert "Springfield" in tool_message.content


def test_runs_several_tools_requested_in_one_reply() -> None:
    first = _call("c1", "search_partners", name="acme")
    second = _call("c2", "low_stock_products")
    llm = FakeLLM([_assistant("", first, second), _assistant("Listo.")])

    turn = _agent(llm).run(history=(), message="dos cosas")

    assert turn.tool_calls == (first, second)
    assert [m.tool_call_id for m in llm.requests[1].messages[-2:]] == ["c1", "c2"]


def test_tool_errors_are_fed_back_to_the_model() -> None:
    llm = FakeLLM([_assistant("", _call("c1", "drop_database")), _assistant("No puedo.")])

    turn = _agent(llm).run(history=(), message="borra todo")

    assert turn.answer == "No puedo."
    assert "Unknown tool" in llm.requests[1].messages[-1].content


def test_stops_after_max_iterations() -> None:
    llm = FakeLLM(
        [_assistant("", _call(f"c{i}", "low_stock_products")) for i in range(MAX_ITERATIONS)]
    )

    turn = _agent(llm).run(history=(), message="bucle")

    assert turn.stop_reason == "max_iterations"
    assert len(llm.requests) == MAX_ITERATIONS
    assert turn.answer
    assert turn.messages[-1] == ChatMessage(role="assistant", content=turn.answer)


def test_stops_when_the_time_budget_runs_out() -> None:
    clock = FakeClock()
    llm = FakeLLM([_assistant("", _call("c1", "low_stock_products")), _assistant("tarde")])
    llm.on_request = lambda: setattr(clock, "now", clock.now + TIMEOUT + 1)

    turn = _agent(llm, clock).run(history=(), message="lento")

    assert turn.stop_reason == "timeout"
    assert len(llm.requests) == 1
    assert turn.messages[-1].role == "assistant"


def test_passes_the_remaining_time_to_the_model() -> None:
    llm = FakeLLM([_assistant("Hola")])

    _agent(llm).run(history=(), message="hola")

    assert llm.requests[0].timeout == pytest.approx(TIMEOUT)


def test_returns_the_history_for_the_next_turn() -> None:
    call = _call("c1", "search_partners", name="acme")
    llm = FakeLLM([_assistant("", call), _assistant("Springfield."), _assistant("De nada.")])
    agent = _agent(llm)

    first = agent.run(history=(), message="¿Dónde está Acme?")
    agent.run(history=first.messages, message="gracias")

    assert [m.role for m in first.messages] == ["user", "assistant", "tool", "assistant"]
    assert llm.requests[2].messages[1:-1] == list(first.messages)
