"""The six questions of the MVP, answered by the real model over the Odoo demo data.

Requires Odoo running (``./scripts/init_odoo.sh``) and ``LLM_API_KEY`` in .env. Each case
checks the tool the agent chose and that the key figures, computed live from Odoo,
appear in the answer. The full case catalogue (``cases.yaml``) arrives in phase 4.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from app.adapters.litellm_client import LiteLLMClient
from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.agent.loop import Agent, AgentTurn
from app.config import Settings
from tests.odoo_rpc import sale_order_count

pytestmark = pytest.mark.evals

NUMBER = re.compile(r"\d[\d.,   ]*\d|\d")
CODE_PREFIX = re.compile(r"^\[[^\]]+\]\s*")


@pytest.fixture(scope="module")
def settings() -> Settings:
    settings = Settings()
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY is not set in .env")
    return settings


@pytest.fixture(scope="module")
def odoo(settings: Settings) -> OdooXmlRpcGateway:
    return OdooXmlRpcGateway.from_settings(settings)


@pytest.fixture(scope="module")
def agent(settings: Settings, odoo: OdooXmlRpcGateway) -> Agent:
    return Agent(
        llm=LiteLLMClient.from_settings(settings),
        gateway=odoo,
        max_iterations=settings.agent_max_iterations,
        timeout_seconds=settings.agent_timeout_seconds,
    )


def _ask(agent: Agent, question: str) -> AgentTurn:
    turn = agent.run(history=(), message=question)
    assert turn.stop_reason == "answered", turn.answer
    return turn


def _tools_used(turn: AgentTurn) -> list[str]:
    return [call.name for call in turn.tool_calls]


def _arguments(turn: AgentTurn, tool: str) -> list[dict[str, object]]:
    return [json.loads(call.arguments) for call in turn.tool_calls if call.name == tool]


def _numbers(text: str) -> set[float]:
    """Every reading of the numbers in ``text`` under both decimal conventions."""
    values: set[float] = set()
    for match in NUMBER.findall(text):
        raw = re.sub(r"[   ]", "", match)
        for thousands, decimal in ((",", "."), (".", ",")):
            candidate = raw.replace(thousands, "").replace(decimal, ".")
            try:
                values.add(float(candidate))
            except ValueError:
                continue
    return values


def _mentions_amount(text: str, amount: float) -> bool:
    tolerance = max(1.0, abs(amount) * 0.005)  # allows rounding to units or "27.7k"-style
    return any(abs(value - amount) <= tolerance for value in _numbers(text))


def _plain_name(name: str) -> str:
    return CODE_PREFIX.sub("", name)


def _month_bounds(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return first, next_first - timedelta(days=1)


def test_sales_this_month_against_last_month(agent: Agent, odoo: OdooXmlRpcGateway) -> None:
    today = datetime.now(UTC).date()
    this_month = _month_bounds(today)
    last_month = _month_bounds(this_month[0] - timedelta(days=1))
    current = odoo.sales_summary(*this_month, group_by="none", limit=10)
    previous = odoo.sales_summary(*last_month, group_by="none", limit=10)

    turn = _ask(agent, "¿Cuánto vendimos este mes y cómo va contra el mes pasado?")

    assert "sales_summary" in _tools_used(turn)
    assert _mentions_amount(turn.answer, current.total_untaxed), turn.answer
    assert _mentions_amount(turn.answer, previous.total_untaxed), turn.answer


def test_top_five_customers(agent: Agent, odoo: OdooXmlRpcGateway) -> None:
    turn = _ask(agent, "¿Quiénes son los 5 clientes que más compran?")

    calls = [
        args for args in _arguments(turn, "sales_summary") if args.get("group_by") == "partner"
    ]
    assert calls, _tools_used(turn)
    # Judge the ranking over the period the model chose.
    args = calls[-1]
    summary = odoo.sales_summary(
        date.fromisoformat(str(args["date_from"])),
        date.fromisoformat(str(args["date_to"])),
        group_by="partner",
        limit=5,
    )
    assert summary.groups
    for group in summary.groups[:3]:
        assert group.key_name in turn.answer, turn.answer


def test_products_below_minimum_stock(agent: Agent, odoo: OdooXmlRpcGateway) -> None:
    expected = odoo.low_stock_products(limit=20)

    turn = _ask(agent, "¿Qué productos están por debajo del stock mínimo?")

    assert "low_stock_products" in _tools_used(turn)
    for item in expected:
        assert _plain_name(item.product_name) in turn.answer, turn.answer


def test_overdue_invoices_of_acme_corporation(agent: Agent, odoo: OdooXmlRpcGateway) -> None:
    [acme] = odoo.search_partners("Acme Corporation", limit=1)
    invoices = odoo.overdue_invoices(acme.id, datetime.now(UTC).date())

    turn = _ask(agent, "¿Qué facturas de Acme Corporation están vencidas?")

    assert "overdue_invoices" in _tools_used(turn)
    assert invoices
    for invoice in invoices:
        assert invoice.name in turn.answer, turn.answer
    total = sum(invoice.amount_residual for invoice in invoices)
    assert _mentions_amount(turn.answer, total), turn.answer


def test_summarise_sale_order(agent: Agent, odoo: OdooXmlRpcGateway) -> None:
    order = odoo.get_sale_order("S00023")
    assert order is not None

    turn = _ask(agent, "Resúmeme el pedido S00023")

    assert "get_sale_order" in _tools_used(turn)
    assert order.partner_name in turn.answer, turn.answer
    assert _mentions_amount(turn.answer, order.amount_total) or _mentions_amount(
        turn.answer, order.amount_untaxed
    ), turn.answer


OFFICE_CHAIRS = {"[FURN_7777] Office Chair", "[FURN_0269] Office Chair Black"}


def test_prepares_a_quotation_but_does_not_create_it(
    agent: Agent, odoo: OdooXmlRpcGateway, settings: Settings
) -> None:
    orders_before = sale_order_count(settings)
    [azure] = odoo.search_partners("Azure Interior", limit=1)

    turn = _ask(agent, "Crea un presupuesto para Azure Interior con 10 sillas de oficina")

    assert sale_order_count(settings) == orders_before
    draft = turn.pending_draft
    if draft is None:
        # Two demo products are office chairs: asking which one is also correct.
        assert all(_plain_name(name) in turn.answer for name in OFFICE_CHAIRS), turn.answer
        return
    assert "prepare_quotation" in _tools_used(turn)
    assert draft.partner_id == azure.id
    assert [(line.product_name in OFFICE_CHAIRS, line.quantity) for line in draft.lines] == [
        (True, 10.0)
    ]
