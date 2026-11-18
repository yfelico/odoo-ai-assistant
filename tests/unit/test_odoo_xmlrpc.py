import xmlrpc.client
from dataclasses import dataclass, field
from datetime import date

import pytest

from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.domain.models import (
    ERPAuthenticationError,
    ERPError,
    ERPUnavailableError,
    Partner,
    SalesGroup,
)

URL = "http://odoo.test:8069"
DB = "demo"
LOGIN = "ai_assistant"
API_KEY = "test-api-key"  # noqa: S105 - fake value for an in-memory server
UID = 7
MARCH = (date(2026, 3, 1), date(2026, 3, 31))
MARCH_DOMAIN = [
    ["state", "=", "sale"],
    ["date_order", ">=", "2026-03-01 00:00:00"],
    ["date_order", "<", "2026-04-01 00:00:00"],
]


@dataclass
class ExecuteCall:
    model: str
    method: str
    args: list[object]
    kwargs: dict[str, object]


@dataclass
class FakeOdooServer:
    """Stands in for both XML-RPC endpoints; replies are consumed in order."""

    uid: int | bool = UID
    replies: list[object] = field(default_factory=list)
    error: Exception | None = None
    execute_error: Exception | None = None
    auth_calls: list[tuple[object, ...]] = field(default_factory=list)
    calls: list[ExecuteCall] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    def version(self) -> dict[str, object]:
        self._maybe_fail()
        return {"server_version": "18.0"}

    def authenticate(
        self, db: str, login: str, password: str, env: dict[str, object]
    ) -> int | bool:
        self._maybe_fail()
        self.auth_calls.append((db, login, password, env))
        return self.uid

    def execute_kw(  # noqa: PLR0913, PLR0917 - mirrors Odoo's execute_kw signature
        self,
        db: str,
        uid: int,
        password: str,
        model: str,
        method: str,
        args: list[object],
        kwargs: dict[str, object] | None = None,
    ) -> object:
        self._maybe_fail()
        if self.execute_error is not None:
            raise self.execute_error
        assert (db, uid, password) == (DB, UID, API_KEY)
        self.calls.append(ExecuteCall(model, method, args, kwargs or {}))
        return self.replies.pop(0)

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error


@pytest.fixture
def server() -> FakeOdooServer:
    return FakeOdooServer()


@pytest.fixture
def gateway(server: FakeOdooServer) -> OdooXmlRpcGateway:
    def factory(url: str) -> FakeOdooServer:
        server.urls.append(url)
        return server

    return OdooXmlRpcGateway(url=URL, db=DB, username=LOGIN, api_key=API_KEY, proxy_factory=factory)


def test_search_partners_sends_ilike_domain_and_maps_false_to_none(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [[{"id": 1, "name": "Deco Addict", "email": False, "city": "Tucson"}]]

    partners = gateway.search_partners("deco", limit=5)

    assert partners == [Partner(id=1, name="Deco Addict", email=None, city="Tucson")]
    assert server.calls == [
        ExecuteCall(
            model="res.partner",
            method="search_read",
            args=[[["name", "ilike", "deco"]]],
            kwargs={"fields": ["id", "name", "email", "city"], "limit": 5},
        )
    ]


def test_authenticates_once_with_api_key_on_the_right_endpoints(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [[], []]

    gateway.search_partners("a", limit=1)
    gateway.search_partners("b", limit=1)

    assert server.auth_calls == [(DB, LOGIN, API_KEY, {})]
    assert set(server.urls) == {f"{URL}/xmlrpc/2/common", f"{URL}/xmlrpc/2/object"}


def test_rejected_credentials_raise_authentication_error(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.uid = False

    with pytest.raises(ERPAuthenticationError):
        gateway.search_partners("deco", limit=5)


def test_network_failure_raises_unavailable(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.error = ConnectionRefusedError()

    with pytest.raises(ERPUnavailableError):
        gateway.search_partners("deco", limit=5)


def test_odoo_fault_raises_erp_error(gateway: OdooXmlRpcGateway, server: FakeOdooServer) -> None:
    server.execute_error = xmlrpc.client.Fault(1, "AccessError")

    with pytest.raises(ERPError):
        gateway.search_partners("deco", limit=5)


def test_sales_summary_without_grouping(gateway: OdooXmlRpcGateway, server: FakeOdooServer) -> None:
    server.replies = [[{"__count": 3, "amount_untaxed": 475.0, "__domain": []}]]

    summary = gateway.sales_summary(*MARCH, group_by="none", limit=10)

    assert (summary.total_untaxed, summary.order_count, summary.groups) == (475.0, 3, ())
    assert server.calls == [
        ExecuteCall(
            model="sale.order",
            method="read_group",
            args=[MARCH_DOMAIN, ["amount_untaxed:sum"], []],
            kwargs={"lazy": False},
        )
    ]


@pytest.mark.parametrize("empty_reply", [[], [{"__count": 0, "amount_untaxed": False}]])
def test_sales_summary_of_empty_period_is_zero(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer, empty_reply: list[object]
) -> None:
    server.replies = [empty_reply]

    summary = gateway.sales_summary(*MARCH, group_by="none", limit=10)

    assert (summary.total_untaxed, summary.order_count) == (0.0, 0)


def test_sales_summary_by_partner(gateway: OdooXmlRpcGateway, server: FakeOdooServer) -> None:
    server.replies = [
        [{"__count": 3, "amount_untaxed": 475.0}],
        [
            {"partner_id": [1, "Acme"], "__count": 2, "amount_untaxed": 425.0},
            {"partner_id": [2, "Globex"], "__count": 1, "amount_untaxed": 50.0},
        ],
    ]

    summary = gateway.sales_summary(*MARCH, group_by="partner", limit=10)

    assert summary.groups == (
        SalesGroup(key_id=1, key_name="Acme", total_untaxed=425.0, order_count=2),
        SalesGroup(key_id=2, key_name="Globex", total_untaxed=50.0, order_count=1),
    )
    assert server.calls[1] == ExecuteCall(
        model="sale.order",
        method="read_group",
        args=[MARCH_DOMAIN, ["amount_untaxed:sum"], ["partner_id"]],
        kwargs={"lazy": False, "orderby": "amount_untaxed desc", "limit": 10},
    )


def test_sales_summary_by_product_counts_distinct_orders(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [
        [{"__count": 3, "amount_untaxed": 475.0}],
        [{"product_id": [10, "Chair"], "__count": 4, "price_subtotal": 175.0, "order_id": 3}],
    ]

    summary = gateway.sales_summary(*MARCH, group_by="product", limit=5)

    assert summary.groups == (
        SalesGroup(key_id=10, key_name="Chair", total_untaxed=175.0, order_count=3),
    )
    assert server.calls[1] == ExecuteCall(
        model="sale.order.line",
        method="read_group",
        args=[
            [
                ["order_id.state", "=", "sale"],
                ["order_id.date_order", ">=", "2026-03-01 00:00:00"],
                ["order_id.date_order", "<", "2026-04-01 00:00:00"],
                ["product_id", "!=", False],
            ],
            ["price_subtotal:sum", "order_id:count_distinct"],
            ["product_id"],
        ],
        kwargs={"lazy": False, "orderby": "price_subtotal desc", "limit": 5},
    )


def test_ping_true_when_server_answers(gateway: OdooXmlRpcGateway) -> None:
    assert gateway.ping() is True


def test_ping_false_when_server_unreachable(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.error = OSError("connection refused")

    assert gateway.ping() is False
