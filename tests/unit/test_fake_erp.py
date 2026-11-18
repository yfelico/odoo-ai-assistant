from datetime import date, datetime

import pytest

from app.adapters.fake_erp import (
    FakeCreatedQuotation,
    FakeERP,
    FakeInvoice,
    FakeOrderLine,
    FakeOrderpoint,
    FakeSaleOrder,
)
from app.domain.models import (
    CreatedQuotation,
    LowStockItem,
    OverdueInvoice,
    Partner,
    Product,
    QuotationLine,
    SaleOrderDetail,
    SaleOrderLine,
)
from tests.contract.erp_gateway_contract import ERPGatewayContract

ACME = Partner(id=1, name="Acme Corp", email="sales@acme.example", city="Springfield")
GLOBEX = Partner(id=2, name="Globex", email=None, city=None)
ACME_DE = Partner(id=3, name="Acme Deutschland", email=None, city="Berlin")

CHAIR = (10, "Office Chair")
DESK = (11, "Desk")
CHAIR_PRODUCT = Product(
    id=10, name="Office Chair", default_code="FURN_0001", list_price=120.0, uom="Units"
)
DESK_PRODUCT = Product(id=11, name="Desk", default_code="FURN_0002", list_price=350.0, uom="Units")
LAMP_PRODUCT = Product(id=12, name="Office Lamp", default_code=None, list_price=40.0, uom="Units")

CHAIR_POINT = FakeOrderpoint(
    product_id=10, product_name="Office Chair", qty_on_hand=1.0, qty_forecast=5.0, min_qty=3.0
)
DESK_POINT = FakeOrderpoint(
    product_id=11, product_name="Desk", qty_on_hand=8.0, qty_forecast=8.0, min_qty=2.0
)
LAMP_POINT = FakeOrderpoint(
    product_id=12, product_name="Office Lamp", qty_on_hand=0.0, qty_forecast=10.0, min_qty=5.0
)

TODAY = date(2026, 11, 18)


def _order(
    order_id: int, partner: Partner, when: datetime, *lines: FakeOrderLine, state: str = "sale"
) -> FakeSaleOrder:
    return FakeSaleOrder(
        id=order_id,
        name=f"S{order_id:05d}",
        partner_id=partner.id,
        partner_name=partner.name,
        date_order=when,
        state=state,
        lines=lines,
    )


def _line(product: tuple[int, str], subtotal: float, quantity: float = 1.0) -> FakeOrderLine:
    return FakeOrderLine(
        product_id=product[0], product_name=product[1], subtotal=subtotal, quantity=quantity
    )


# Test builder mirroring the invoice fields.
def _invoice(  # noqa: PLR0913
    invoice_id: int,
    partner: Partner,
    due: date,
    residual: float,
    total: float,
    **status: str,
) -> FakeInvoice:
    return FakeInvoice(
        invoice=OverdueInvoice(
            id=invoice_id,
            name=f"INV/{invoice_id}",
            partner_id=partner.id,
            partner_name=partner.name,
            due_date=due,
            amount_residual=residual,
            amount_total=total,
        ),
        **status,
    )


@pytest.fixture
def erp() -> FakeERP:
    return FakeERP(
        partners=[ACME, GLOBEX, ACME_DE],
        orders=[
            _order(1, ACME, datetime(2026, 3, 1, 9), _line(CHAIR, 100.0, 2.0), _line(DESK, 300.0)),
            _order(2, GLOBEX, datetime(2026, 3, 15, 12), _line(CHAIR, 50.0)),
            # Last minute of the period: an inclusive date_to must count it.
            _order(3, ACME, datetime(2026, 3, 31, 23, 59), _line(CHAIR, 25.0)),
            _order(4, GLOBEX, datetime(2026, 3, 20), _line(DESK, 999.0), state="draft"),
            _order(5, ACME, datetime(2026, 3, 21), _line(DESK, 999.0), state="cancel"),
            _order(6, GLOBEX, datetime(2026, 4, 1), _line(DESK, 999.0)),
        ],
        products=[CHAIR_PRODUCT, DESK_PRODUCT, LAMP_PRODUCT],
        orderpoints=[CHAIR_POINT, DESK_POINT, LAMP_POINT],
        invoices=[
            _invoice(1, ACME, date(2026, 10, 1), 100.0, 150.0),
            _invoice(2, GLOBEX, date(2026, 11, 1), 40.0, 90.0, payment_state="partial"),
            _invoice(3, ACME, date(2026, 12, 31), 70.0, 70.0),  # not due yet
            _invoice(4, GLOBEX, TODAY, 10.0, 10.0),  # due today: not overdue yet
            _invoice(5, ACME, date(2026, 9, 1), 0.0, 80.0, payment_state="paid"),
            _invoice(6, ACME, date(2026, 9, 1), 80.0, 80.0, state="draft"),
            _invoice(7, ACME, date(2026, 9, 1), 80.0, 80.0, move_type="out_refund"),
        ],
    )


MARCH = (date(2026, 3, 1), date(2026, 3, 31))


class TestFakeERPContract(ERPGatewayContract):
    partner_query = "acme"
    sales_period = MARCH
    product_query = "office"
    order_name = "S00001"
    overdue_as_of = TODAY

    @pytest.fixture
    def gateway(self, erp: FakeERP) -> FakeERP:
        return erp


def test_search_partners_returns_partner_fields(erp: FakeERP) -> None:
    assert erp.search_partners("globex", limit=5) == [GLOBEX]


def test_sales_summary_counts_only_confirmed_orders_with_inclusive_end(erp: FakeERP) -> None:
    summary = erp.sales_summary(*MARCH, group_by="none", limit=10)

    assert summary.total_untaxed == pytest.approx(475.0)
    assert summary.order_count == 3


def test_sales_summary_by_partner(erp: FakeERP) -> None:
    summary = erp.sales_summary(*MARCH, group_by="partner", limit=10)

    assert [(g.key_id, g.key_name, g.total_untaxed, g.order_count) for g in summary.groups] == [
        (ACME.id, ACME.name, 425.0, 2),
        (GLOBEX.id, GLOBEX.name, 50.0, 1),
    ]


def test_sales_summary_by_product_counts_distinct_orders(erp: FakeERP) -> None:
    summary = erp.sales_summary(*MARCH, group_by="product", limit=10)

    assert [(g.key_id, g.key_name, g.total_untaxed, g.order_count) for g in summary.groups] == [
        (DESK[0], DESK[1], 300.0, 1),
        (CHAIR[0], CHAIR[1], 175.0, 3),
    ]


def test_ping_reflects_reachability() -> None:
    assert FakeERP(partners=[], orders=[]).ping() is True
    assert FakeERP(partners=[], orders=[], reachable=False).ping() is False


def test_search_products_matches_reference(erp: FakeERP) -> None:
    assert erp.search_products("furn_0002", limit=5) == [DESK_PRODUCT]


def test_low_stock_products_lists_items_below_minimum_by_shortfall(erp: FakeERP) -> None:
    assert erp.low_stock_products(limit=20) == [
        LowStockItem(
            product_id=12,
            product_name="Office Lamp",
            qty_on_hand=0.0,
            qty_forecast=10.0,
            min_qty=5.0,
        ),
        LowStockItem(
            product_id=10,
            product_name="Office Chair",
            qty_on_hand=1.0,
            qty_forecast=5.0,
            min_qty=3.0,
        ),
    ]


def test_overdue_invoices_only_posted_unpaid_customer_invoices(erp: FakeERP) -> None:
    assert [inv.name for inv in erp.overdue_invoices(None, TODAY)] == ["INV/1", "INV/2"]


def test_overdue_invoices_of_one_partner(erp: FakeERP) -> None:
    assert [inv.name for inv in erp.overdue_invoices(GLOBEX.id, TODAY)] == ["INV/2"]


def test_get_sale_order_details(erp: FakeERP) -> None:
    assert erp.get_sale_order("S00001") == SaleOrderDetail(
        id=1,
        name="S00001",
        partner_id=ACME.id,
        partner_name=ACME.name,
        date_order=datetime(2026, 3, 1, 9),
        state="sale",
        amount_untaxed=400.0,
        amount_total=400.0,
        lines=(
            SaleOrderLine(
                product_id=10,
                product_name="Office Chair",
                description="Office Chair",
                quantity=2.0,
                price_unit=50.0,
                subtotal=100.0,
            ),
            SaleOrderLine(
                product_id=11,
                product_name="Desk",
                description="Desk",
                quantity=1.0,
                price_unit=300.0,
                subtotal=300.0,
            ),
        ),
    )


def test_create_quotation_records_the_request_and_prices_at_list_price(erp: FakeERP) -> None:
    line = QuotationLine(
        product_id=10, product_name="Office Chair", quantity=2.0, price_unit=1.0, subtotal=2.0
    )

    created = erp.create_quotation(ACME.id, (line,), "note")

    assert erp.created_quotations == [
        FakeCreatedQuotation(partner_id=ACME.id, lines=(line,), note="note")
    ]
    assert created == CreatedQuotation(
        id=created.id, name=created.name, amount_untaxed=240.0, amount_total=240.0
    )
    assert created.name.startswith("S")
