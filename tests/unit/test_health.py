from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake_erp import FakeERP
from app.api.routes import get_erp_gateway
from app.main import app


@pytest.fixture
def client_with_erp() -> Iterator[tuple[TestClient, list[FakeERP]]]:
    holder: list[FakeERP] = [FakeERP(partners=[], orders=[])]
    app.dependency_overrides[get_erp_gateway] = lambda: holder[0]
    yield TestClient(app), holder
    app.dependency_overrides.clear()


def test_health_reports_odoo_ok(client_with_erp: tuple[TestClient, list[FakeERP]]) -> None:
    client, _ = client_with_erp

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "odoo": "ok"}


def test_health_reports_odoo_unreachable(
    client_with_erp: tuple[TestClient, list[FakeERP]],
) -> None:
    client, holder = client_with_erp
    holder[0] = FakeERP(partners=[], orders=[], reachable=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "odoo": "unreachable"}
