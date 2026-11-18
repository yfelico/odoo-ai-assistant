"""Runs the ERPGateway contract against the Odoo demo database.

Requires ``docker compose up -d`` and ``./scripts/init_odoo.sh`` (which fills
ODOO_API_KEY in .env).
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.config import Settings
from app.main import app
from tests.contract.erp_gateway_contract import ERPGatewayContract

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

    @pytest.fixture
    def gateway(self, odoo: OdooXmlRpcGateway) -> OdooXmlRpcGateway:
        return odoo


def test_finds_demo_customer(odoo: OdooXmlRpcGateway) -> None:
    names = [p.name for p in odoo.search_partners("azure interior", limit=5)]

    assert "Azure Interior" in names


def test_health_reaches_odoo() -> None:
    response = TestClient(app).get("/health")

    assert response.json() == {"status": "ok", "odoo": "ok"}
