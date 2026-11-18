"""Runs the ERPGateway contract against the Odoo demo database.

Requires ``docker compose up -d`` and ``./scripts/init_odoo.sh`` (which fills
ODOO_API_KEY in .env).
"""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.config import Settings
from app.domain.models import QuotationLine
from app.main import app
from tests.contract.erp_gateway_contract import ERPGatewayContract
from tests.odoo_rpc import odoo_execute

pytestmark = pytest.mark.integration

# Odoo generates demo sale orders relative to the installation date.
_TODAY = date.today()
DEMO_PERIOD = (_TODAY - timedelta(days=730), _TODAY + timedelta(days=30))


@pytest.fixture(scope="module")
def odoo() -> OdooXmlRpcGateway:
    return OdooXmlRpcGateway.from_settings(Settings())


class TestOdooContract(ERPGatewayContract):
    partner_query = "in"  # e.g. "Azure Interior", "Gemini Furniture"
    sales_period = DEMO_PERIOD
    product_query = "desk"
    order_name = "S00023"
    overdue_as_of = _TODAY

    @pytest.fixture
    def gateway(self, odoo: OdooXmlRpcGateway) -> OdooXmlRpcGateway:
        return odoo


def test_finds_demo_customer(odoo: OdooXmlRpcGateway) -> None:
    names = [p.name for p in odoo.search_partners("azure interior", limit=5)]

    assert "Azure Interior" in names


def test_finds_product_by_reference(odoo: OdooXmlRpcGateway) -> None:
    products = odoo.search_products("furn_7777", limit=5)

    assert [p.default_code for p in products] == ["FURN_7777"]


def test_demo_replenishment_rules_are_below_minimum(odoo: OdooXmlRpcGateway) -> None:
    names = {item.product_name for item in odoo.low_stock_products(limit=20)}

    assert {"[FURN_7777] Office Chair", "[FURN_8888] Office Lamp"} <= names


def test_acme_corporation_has_overdue_invoices(odoo: OdooXmlRpcGateway) -> None:
    [acme] = odoo.search_partners("acme corporation", limit=1)

    invoices = odoo.overdue_invoices(acme.id, _TODAY)

    assert invoices
    assert {inv.partner_name for inv in invoices} == {"Acme Corporation"}


def test_reads_demo_sale_order(odoo: OdooXmlRpcGateway) -> None:
    order = odoo.get_sale_order("S00023")

    assert order is not None
    assert order.partner_name == "Gemini Furniture"
    assert all(line.quantity > 0 for line in order.lines)


def test_health_reaches_odoo() -> None:
    response = TestClient(app).get("/health")

    assert response.json() == {"status": "ok", "odoo": "ok"}


INTEGRATION_NOTE = "Creado por el asistente de IA tras confirmación del usuario (prueba)"


@pytest.fixture
def created_quotation_ids() -> Iterator[list[int]]:
    """Cancels the quotations a test creates: the technical user cannot delete them."""
    ids: list[int] = []
    yield ids
    if ids:
        odoo_execute(Settings(), "sale.order", "action_cancel", [ids])


def test_creates_a_quotation_with_a_chatter_note(
    odoo: OdooXmlRpcGateway, created_quotation_ids: list[int]
) -> None:
    [azure] = odoo.search_partners("azure interior", limit=1)
    [chair] = odoo.search_products("furn_7777", limit=1)
    line = QuotationLine(
        product_id=chair.id,
        product_name=chair.name,
        quantity=10.0,
        price_unit=chair.list_price,
        subtotal=10 * chair.list_price,
    )

    created = odoo.create_quotation(azure.id, (line,), INTEGRATION_NOTE)
    created_quotation_ids.append(created.id)

    order = odoo.get_sale_order(created.name)
    assert order is not None
    assert (order.partner_id, order.state) == (azure.id, "draft")
    assert [(item.product_id, item.quantity) for item in order.lines] == [(chair.id, 10.0)]
    assert created.amount_untaxed == pytest.approx(order.amount_untaxed)
    messages = odoo_execute(
        Settings(),
        "mail.message",
        "search_read",
        [[["model", "=", "sale.order"], ["res_id", "=", created.id]]],
        {"fields": ["body"]},
    )
    assert isinstance(messages, list)
    assert any(INTEGRATION_NOTE in str(message["body"]) for message in messages)
