from datetime import date, datetime

import pytest

from app.adapters.fake_erp import FakeERP, FakeOrderLine, FakeSaleOrder
from app.domain.models import Partner
from tests.contract.erp_gateway_contract import ERPGatewayContract

ACME = Partner(id=1, name="Acme Corp", email="sales@acme.example", city="Springfield")
GLOBEX = Partner(id=2, name="Globex", email=None, city=None)
ACME_DE = Partner(id=3, name="Acme Deutschland", email=None, city="Berlin")

CHAIR = (10, "Office Chair")
DESK = (11, "Desk")


def _order(
    order_id: int, partner: Partner, when: datetime, *lines: FakeOrderLine, state: str = "sale"
) -> FakeSaleOrder:
    return FakeSaleOrder(
        id=order_id,
        partner_id=partner.id,
        partner_name=partner.name,
        date_order=when,
        state=state,
        lines=lines,
    )


def _line(product: tuple[int, str], subtotal: float) -> FakeOrderLine:
    return FakeOrderLine(product_id=product[0], product_name=product[1], subtotal=subtotal)


@pytest.fixture
def erp() -> FakeERP:
    return FakeERP(
        partners=[ACME, GLOBEX, ACME_DE],
        orders=[
            _order(1, ACME, datetime(2026, 3, 1, 9), _line(CHAIR, 100.0), _line(DESK, 300.0)),
            _order(2, GLOBEX, datetime(2026, 3, 15, 12), _line(CHAIR, 50.0)),
            # Last minute of the period: an inclusive date_to must count it.
            _order(3, ACME, datetime(2026, 3, 31, 23, 59), _line(CHAIR, 25.0)),
            _order(4, GLOBEX, datetime(2026, 3, 20), _line(DESK, 999.0), state="draft"),
            _order(5, ACME, datetime(2026, 3, 21), _line(DESK, 999.0), state="cancel"),
            _order(6, GLOBEX, datetime(2026, 4, 1), _line(DESK, 999.0)),
        ],
    )


MARCH = (date(2026, 3, 1), date(2026, 3, 31))


class TestFakeERPContract(ERPGatewayContract):
    partner_query = "acme"
    sales_period = MARCH

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
