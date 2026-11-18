import json
import logging
from datetime import date

import pytest

from app.adapters.fake_erp import FakeERP
from app.adapters.fake_llm import FakeLLM
from app.agent.audit import AUDIT_LOGGER, redact_arguments
from app.agent.drafts import DraftStore
from app.agent.loop import Agent
from app.agent.tools import create_quotation
from app.domain.models import ChatMessage, Partner, QuotationLine, ToolCall

ACME = Partner(id=1, name="Acme Corp", email="sales@acme.example", city="Springfield")


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == AUDIT_LOGGER]


def test_redaction_keeps_ids_and_numbers_and_hides_text() -> None:
    arguments = json.dumps(
        {
            "name": "Acme Corp",
            "limit": 5,
            "partner_id": None,
            "lines": [{"product_id": 5, "quantity": 2.5}],
        }
    )

    assert redact_arguments(arguments) == {
        "name": "<text:9>",
        "limit": 5,
        "partner_id": None,
        "lines": [{"product_id": 5, "quantity": 2.5}],
    }


def test_redaction_of_invalid_json_keeps_only_its_length() -> None:
    assert redact_arguments("{broken") == "<invalid-json:7>"


def test_every_tool_call_is_audited_without_personal_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    calls = (
        ToolCall(id="c1", name="search_partners", arguments='{"name": "Acme Corp"}'),
        ToolCall(id="c2", name="drop_database", arguments="{}"),
    )
    llm = FakeLLM(
        [
            ChatMessage(role="assistant", tool_calls=calls),
            ChatMessage(role="assistant", content="ok"),
        ]
    )
    agent = Agent(
        llm=llm,
        gateway=FakeERP(partners=[ACME], orders=[]),
        max_iterations=6,
        timeout_seconds=60,
        today=lambda: date(2026, 11, 18),
    )

    agent.run(history=(), message="hola", conversation_id="conv-7")

    events = _events(caplog)
    assert [(e["event"], e["conversation_id"], e["tool"], e["outcome"]) for e in events] == [
        ("tool_call", "conv-7", "search_partners", "ok"),
        ("tool_call", "conv-7", "drop_database", "error"),
    ]
    assert events[0]["arguments"] == {"name": "<text:9>"}
    assert all(isinstance(e["duration_ms"], int | float) for e in events)
    assert "Acme" not in caplog.text


def test_confirmed_quotations_are_audited(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    drafts = DraftStore(ttl_seconds=900)
    line = QuotationLine(product_id=5, product_name="Chair", quantity=1, price_unit=1, subtotal=1)
    draft = drafts.create(partner_id=ACME.id, partner_name=ACME.name, lines=(line,))

    created = create_quotation(draft.id, drafts, FakeERP(partners=[ACME], orders=[]))

    [event] = _events(caplog)
    assert event == {
        "event": "quotation_created",
        "draft_id": draft.id,
        "partner_id": ACME.id,
        "quotation_id": created.id,
        "outcome": "ok",
        "duration_ms": event["duration_ms"],
    }
