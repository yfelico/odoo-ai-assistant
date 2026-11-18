import xmlrpc.client
from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.domain.models import (
    CreatedQuotation,
    ERPAuthenticationError,
    ERPError,
    ERPUnavailableError,
    LowStockItem,
    OverdueInvoice,
    Partner,
    Product,
    QuotationLine,
    SaleOrderDetail,
    SaleOrderLine,
    SalesGroup,
)

URL = "http://odoo.test:8069"
DB = "demo"
LOGIN = "ai_assistant"
# Fake value for an in-memory server.
API_KEY = "test-api-key"  # noqa: S105
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

    # Mirrors Odoo's execute_kw signature.
    def execute_kw(  # noqa: PLR0913, PLR0917
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
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

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


def test_search_products_matches_name_or_reference_of_saleable_products(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [
        [
            {
                "id": 5,
                "display_name": "[FURN_7777] Office Chair",
                "default_code": "FURN_7777",
                "lst_price": 70.0,
                "uom_id": [1, "Units"],
            },
            {
                "id": 6,
                "display_name": "Lamp",
                "default_code": False,
                "lst_price": 40,
                "uom_id": [1, "Units"],
            },
        ]
    ]

    products = gateway.search_products("chair", limit=5)

    assert products == [
        Product(
            id=5,
            name="[FURN_7777] Office Chair",
            default_code="FURN_7777",
            list_price=70.0,
            uom="Units",
        ),
        Product(id=6, name="Lamp", default_code=None, list_price=40.0, uom="Units"),
    ]
    assert server.calls == [
        ExecuteCall(
            model="product.product",
            method="search_read",
            args=[
                [
                    "&",
                    ["sale_ok", "=", True],
                    "|",
                    ["name", "ilike", "chair"],
                    ["default_code", "ilike", "chair"],
                ]
            ],
            kwargs={
                "fields": ["id", "display_name", "default_code", "lst_price", "uom_id"],
                "limit": 5,
            },
        )
    ]


def _orderpoint(product: list[object], on_hand: float, minimum: float) -> dict[str, object]:
    return {
        "id": 1,
        "product_id": product,
        "qty_on_hand": on_hand,
        "qty_forecast": on_hand + 10,
        "product_min_qty": minimum,
    }


def test_low_stock_products_filters_and_sorts_in_python(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [
        [
            _orderpoint([5, "Office Chair"], on_hand=1.0, minimum=3.0),
            _orderpoint([6, "Office Lamp"], on_hand=0.0, minimum=5.0),
            _orderpoint([7, "Desk"], on_hand=8.0, minimum=2.0),
            _orderpoint([8, "Flipover"], on_hand=0.0, minimum=1.0),
        ]
    ]

    items = gateway.low_stock_products(limit=2)

    assert items == [
        LowStockItem(
            product_id=6,
            product_name="Office Lamp",
            qty_on_hand=0.0,
            qty_forecast=10.0,
            min_qty=5.0,
        ),
        LowStockItem(
            product_id=5,
            product_name="Office Chair",
            qty_on_hand=1.0,
            qty_forecast=11.0,
            min_qty=3.0,
        ),
    ]
    # qty_on_hand is not stored, so Odoo cannot filter on it.
    assert server.calls == [
        ExecuteCall(
            model="stock.warehouse.orderpoint",
            method="search_read",
            args=[[]],
            kwargs={"fields": ["product_id", "qty_on_hand", "qty_forecast", "product_min_qty"]},
        )
    ]


OVERDUE_DOMAIN = [
    ["move_type", "=", "out_invoice"],
    ["state", "=", "posted"],
    ["payment_state", "in", ["not_paid", "partial"]],
    ["invoice_date_due", "<", "2026-11-18"],
]
INVOICE_FIELDS = [
    "id",
    "name",
    "commercial_partner_id",
    "invoice_date_due",
    "amount_residual",
    "amount_total",
]


def test_overdue_invoices(gateway: OdooXmlRpcGateway, server: FakeOdooServer) -> None:
    server.replies = [
        [
            {
                "id": 4,
                "name": "INV/2026/00004",
                "commercial_partner_id": [10, "Acme Corporation"],
                "invoice_date_due": "2026-11-03",
                "amount_residual": 31750.0,
                "amount_total": 31750.0,
            }
        ]
    ]

    invoices = gateway.overdue_invoices(None, date(2026, 11, 18))

    assert invoices == [
        OverdueInvoice(
            id=4,
            name="INV/2026/00004",
            partner_id=10,
            partner_name="Acme Corporation",
            due_date=date(2026, 11, 3),
            amount_residual=31750.0,
            amount_total=31750.0,
        )
    ]
    assert server.calls == [
        ExecuteCall(
            model="account.move",
            method="search_read",
            args=[OVERDUE_DOMAIN],
            kwargs={"fields": INVOICE_FIELDS, "order": "invoice_date_due asc, id asc"},
        )
    ]


def test_overdue_invoices_of_a_partner_include_its_contacts(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [[]]

    gateway.overdue_invoices(10, date(2026, 11, 18))

    assert server.calls[0].args == [[*OVERDUE_DOMAIN, ["partner_id", "child_of", 10]]]


def test_get_sale_order_reads_header_and_product_lines(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [
        [
            {
                "id": 23,
                "name": "S00023",
                "partner_id": [11, "Gemini Furniture"],
                "date_order": "2026-11-18 02:20:53",
                "state": "sale",
                "amount_untaxed": 4350.0,
                "amount_total": 4350.0,
            }
        ],
        [
            {
                "id": 48,
                "product_id": [32, "Acoustic Bloc Screens"],
                "name": "Acoustic Bloc Screens (White)",
                "product_uom_qty": 10.0,
                "price_unit": 295.0,
                "price_subtotal": 2950.0,
            }
        ],
    ]

    order = gateway.get_sale_order("s00023")

    assert order == SaleOrderDetail(
        id=23,
        name="S00023",
        partner_id=11,
        partner_name="Gemini Furniture",
        date_order=datetime(2026, 11, 18, 2, 20, 53),
        state="sale",
        amount_untaxed=4350.0,
        amount_total=4350.0,
        lines=(
            SaleOrderLine(
                product_id=32,
                product_name="Acoustic Bloc Screens",
                description="Acoustic Bloc Screens (White)",
                quantity=10.0,
                price_unit=295.0,
                subtotal=2950.0,
            ),
        ),
    )
    assert server.calls == [
        ExecuteCall(
            model="sale.order",
            method="search_read",
            args=[[["name", "=ilike", "s00023"]]],
            kwargs={
                "fields": [
                    "id",
                    "name",
                    "partner_id",
                    "date_order",
                    "state",
                    "amount_untaxed",
                    "amount_total",
                ],
                "limit": 1,
            },
        ),
        ExecuteCall(
            model="sale.order.line",
            method="search_read",
            args=[[["order_id", "=", 23], ["display_type", "=", False]]],
            kwargs={
                "fields": ["product_id", "name", "product_uom_qty", "price_unit", "price_subtotal"],
                "order": "sequence, id",
            },
        ),
    ]


def test_get_sale_order_escapes_like_wildcards(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [[]]

    assert gateway.get_sale_order(r"S%_\1") is None
    assert server.calls[0].args == [[["name", "=ilike", r"S\%\_\\1"]]]


def test_get_partner_reads_by_id(gateway: OdooXmlRpcGateway, server: FakeOdooServer) -> None:
    server.replies = [[{"id": 15, "name": "Azure Interior", "email": False, "city": "Fremont"}]]

    assert gateway.get_partner(15) == Partner(
        id=15, name="Azure Interior", email=None, city="Fremont"
    )
    assert server.calls == [
        ExecuteCall(
            model="res.partner",
            method="search_read",
            args=[[["id", "=", 15]]],
            kwargs={"fields": ["id", "name", "email", "city"], "limit": 1},
        )
    ]


def test_get_partner_unknown_returns_none(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [[]]

    assert gateway.get_partner(999) is None


def test_get_products_reads_saleable_products_by_id(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [[]]

    assert gateway.get_products([5, 6]) == []
    assert server.calls == [
        ExecuteCall(
            model="product.product",
            method="search_read",
            args=[[["id", "in", [5, 6]], ["sale_ok", "=", True]]],
            kwargs={"fields": ["id", "display_name", "default_code", "lst_price", "uom_id"]},
        )
    ]


QUOTATION_LINE = QuotationLine(
    product_id=5, product_name="Office Chair", quantity=10.0, price_unit=70.0, subtotal=700.0
)
CREATED_ROW = {"id": 42, "name": "S00042", "amount_untaxed": 700.0, "amount_total": 805.0}


def test_create_quotation_creates_the_order_logs_a_note_and_reads_totals(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [42, 101, [CREATED_ROW]]

    created = gateway.create_quotation(15, (QUOTATION_LINE,), "Created by the assistant")

    assert created == CreatedQuotation(
        id=42, name="S00042", amount_untaxed=700.0, amount_total=805.0
    )
    assert server.calls == [
        ExecuteCall(
            model="sale.order",
            method="create",
            args=[
                {
                    "partner_id": 15,
                    "order_line": [[0, 0, {"product_id": 5, "product_uom_qty": 10.0}]],
                }
            ],
            kwargs={},
        ),
        ExecuteCall(
            model="sale.order",
            method="message_post",
            args=[[42]],
            kwargs={
                "body": "Created by the assistant",
                "message_type": "comment",
                "subtype_xmlid": "mail.mt_note",
            },
        ),
        ExecuteCall(
            model="sale.order",
            method="search_read",
            args=[[["id", "=", 42]]],
            kwargs={"fields": ["id", "name", "amount_untaxed", "amount_total"], "limit": 1},
        ),
    ]


def test_create_quotation_still_returns_the_order_when_the_note_fails(
    gateway: OdooXmlRpcGateway, server: FakeOdooServer
) -> None:
    server.replies = [42, xmlrpc.client.Fault(2, "denied"), [CREATED_ROW]]

    created = gateway.create_quotation(15, (QUOTATION_LINE,), "Created by the assistant")

    assert created.id == 42
