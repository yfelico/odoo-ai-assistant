import json
import logging
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake_erp import FakeERP
from app.adapters.fake_llm import FakeLLM
from app.agent.audit import AUDIT_LOGGER
from app.agent.conversations import ConversationStore
from app.agent.drafts import DraftStore
from app.agent.loop import Agent
from app.agent.tools import QUOTATION_NOTE
from app.api.routes import get_agent, get_conversation_store, get_draft_store, get_erp_gateway
from app.domain.models import (
    ChatMessage,
    CreatedQuotation,
    ERPError,
    ERPUnavailableError,
    Partner,
    Product,
    QuotationLine,
    ToolCall,
)
from app.main import app

AZURE = Partner(id=15, name="Azure Interior", email=None, city="Fremont")
CHAIR = Product(id=5, name="Office Chair", default_code="FURN_7777", list_price=70.0, uom="Units")
LINE = QuotationLine(
    product_id=5, product_name="Office Chair", quantity=10, price_unit=70, subtotal=700
)
TTL = 900.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FlakyERP(FakeERP):
    """Fails the next quotation creations with the queued errors."""

    def __init__(self, errors: list[ERPError]) -> None:
        super().__init__(partners=[AZURE], orders=[], products=[CHAIR])
        self.errors = errors

    def create_quotation(
        self, partner_id: int, lines: tuple[QuotationLine, ...], note: str
    ) -> CreatedQuotation:
        if self.errors:
            raise self.errors.pop(0)
        return super().create_quotation(partner_id, lines, note)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def erp() -> FlakyERP:
    return FlakyERP(errors=[])


@pytest.fixture
def drafts(clock: FakeClock) -> DraftStore:
    return DraftStore(ttl_seconds=TTL, clock=clock)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM([])


@pytest.fixture
def client(llm: FakeLLM, erp: FlakyERP, drafts: DraftStore) -> Iterator[TestClient]:
    agent = Agent(
        llm=llm,
        gateway=erp,
        max_iterations=6,
        timeout_seconds=60,
        today=lambda: date(2026, 11, 18),
        drafts=drafts,
    )
    conversations = ConversationStore(ttl_seconds=3600)
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_conversation_store] = lambda: conversations
    app.dependency_overrides[get_draft_store] = lambda: drafts
    app.dependency_overrides[get_erp_gateway] = lambda: erp
    yield TestClient(app)
    app.dependency_overrides.clear()


def _draft_id(drafts: DraftStore) -> str:
    return drafts.create(partner_id=AZURE.id, partner_name=AZURE.name, lines=(LINE,)).id


def test_chat_returns_the_pending_draft(client: TestClient, llm: FakeLLM) -> None:
    prepare = ToolCall(
        id="c1",
        name="prepare_quotation",
        arguments=json.dumps({"partner_id": 15, "lines": [{"product_id": 5, "quantity": 10}]}),
    )
    llm.replies.extend(
        [
            ChatMessage(role="assistant", tool_calls=(prepare,)),
            ChatMessage(role="assistant", content="¿Confirmas el presupuesto?"),
        ]
    )

    draft = client.post("/chat", json={"message": "10 sillas para Azure"}).json()["pending_draft"]

    assert draft == {
        "draft_id": draft["draft_id"],
        "partner_id": 15,
        "partner_name": "Azure Interior",
        "lines": [
            {
                "product_id": 5,
                "product_name": "Office Chair",
                "quantity": 10.0,
                "price_unit": 70.0,
                "subtotal": 700.0,
            }
        ],
        "estimated_total_untaxed": 700.0,
    }


def test_confirm_creates_the_quotation(
    client: TestClient, erp: FlakyERP, drafts: DraftStore
) -> None:
    response = client.post("/confirm", json={"draft_id": _draft_id(drafts)})

    assert response.status_code == 200
    body = response.json()
    assert body["name"].startswith("S")
    assert body == {
        "quotation_id": body["quotation_id"],
        "name": body["name"],
        "amount_untaxed": 700.0,
        "amount_total": 700.0,
    }
    assert len(erp.created_quotations) == 1


def test_a_draft_can_only_be_confirmed_once(
    client: TestClient, erp: FlakyERP, drafts: DraftStore
) -> None:
    draft_id = _draft_id(drafts)
    client.post("/confirm", json={"draft_id": draft_id})

    response = client.post("/confirm", json={"draft_id": draft_id})

    assert response.status_code == 404
    assert len(erp.created_quotations) == 1


def test_unknown_draft_is_not_found(client: TestClient, erp: FlakyERP) -> None:
    response = client.post("/confirm", json={"draft_id": "nope"})

    assert response.status_code == 404
    assert response.json()["detail"]
    assert erp.created_quotations == []


def test_expired_draft_is_not_found(
    client: TestClient, erp: FlakyERP, drafts: DraftStore, clock: FakeClock
) -> None:
    draft_id = _draft_id(drafts)
    clock.now = TTL + 1

    response = client.post("/confirm", json={"draft_id": draft_id})

    assert response.status_code == 404
    assert erp.created_quotations == []


def test_odoo_down_is_service_unavailable_and_the_draft_can_be_retried(
    client: TestClient, erp: FlakyERP, drafts: DraftStore
) -> None:
    erp.errors.append(ERPUnavailableError("Odoo is unreachable"))
    draft_id = _draft_id(drafts)

    first = client.post("/confirm", json={"draft_id": draft_id})
    retry = client.post("/confirm", json={"draft_id": draft_id})

    assert first.status_code == 503
    assert retry.status_code == 200
    assert len(erp.created_quotations) == 1


def test_odoo_rejection_is_bad_gateway(
    client: TestClient, erp: FlakyERP, drafts: DraftStore
) -> None:
    erp.errors.append(ERPError("Odoo rejected the request (fault 2)"))

    response = client.post("/confirm", json={"draft_id": _draft_id(drafts)})

    assert response.status_code == 502
    assert erp.created_quotations == []


def test_confirm_records_the_odoo_user_who_confirmed(
    client: TestClient, erp: FlakyERP, drafts: DraftStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)

    response = client.post(
        "/confirm", json={"draft_id": _draft_id(drafts), "confirmed_by_user_id": 7}
    )

    assert response.status_code == 200
    [created] = erp.created_quotations
    assert created.note == f"{QUOTATION_NOTE} #7"
    [event] = [json.loads(r.getMessage()) for r in caplog.records if r.name == AUDIT_LOGGER]
    assert event["confirmed_by_user_id"] == 7


@pytest.mark.parametrize("user_id", [0, -1, "admin"])
def test_confirm_rejects_invalid_user_ids(
    client: TestClient, drafts: DraftStore, user_id: object
) -> None:
    response = client.post(
        "/confirm", json={"draft_id": _draft_id(drafts), "confirmed_by_user_id": user_id}
    )

    assert response.status_code == 422
