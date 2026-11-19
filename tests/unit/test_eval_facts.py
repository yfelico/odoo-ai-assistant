import json
from datetime import date, datetime

import pytest

from app.adapters.fake_erp import FakeERP, FakeOrderLine, FakeSaleOrder
from app.agent.loop import AgentTurn
from app.domain.models import Partner, Product, ToolCall
from tests.evals.cases import Fact
from tests.evals.facts import FactError, FactResolver, period_bounds

TODAY = date(2026, 11, 18)
ACME = Partner(id=1, name="Acme Corporation", email="acme@example.com", city="Pleasant Hill")
AZURE = Partner(id=2, name="Azure Interior", email=None, city="Fremont")
CHAIR = Product(
    id=5, name="[FURN_7777] Office Chair", default_code="FURN_7777", list_price=70.0, uom="Units"
)
NO_TURN = AgentTurn(answer="", stop_reason="answered", tool_calls=(), messages=())


def _order(name: str, partner: Partner, day: date, total: float) -> FakeSaleOrder:
    return FakeSaleOrder(
        id=int(name[1:]),
        partner_id=partner.id,
        partner_name=partner.name,
        date_order=datetime(day.year, day.month, day.day, 10),
        state="sale",
        lines=(FakeOrderLine(product_id=5, product_name=CHAIR.name, subtotal=total),),
        name=name,
    )


def _resolver() -> FactResolver:
    erp = FakeERP(
        partners=[ACME, AZURE],
        orders=[
            _order("S1", ACME, date(2026, 11, 2), 300.0),
            _order("S2", AZURE, date(2026, 11, 5), 100.0),
            _order("S3", ACME, date(2026, 10, 20), 50.0),
        ],
        products=[CHAIR],
    )
    return FactResolver(gateway=erp, today=TODAY)


@pytest.mark.parametrize(
    ("period", "bounds"),
    [
        ("this_month", (date(2026, 11, 1), date(2026, 11, 30))),
        ("last_month", (date(2026, 10, 1), date(2026, 10, 31))),
        ("this_year", (date(2026, 1, 1), TODAY)),
    ],
)
def test_period_bounds(period: str, bounds: tuple[date, date]) -> None:
    assert period_bounds(period, TODAY) == bounds  # type: ignore[arg-type]


def test_sales_facts() -> None:
    resolve = _resolver()

    assert resolve(Fact(fact="sales_total", period="this_month"), NO_TURN) == 400.0
    assert resolve(Fact(fact="order_count", period="this_year"), NO_TURN) == 3.0
    assert (
        resolve(
            Fact(fact="customer_sales", partner="Acme Corporation", period="this_year"), NO_TURN
        )
        == 350.0
    )
    assert resolve(Fact(fact="top_product", period="this_month"), NO_TURN) == [CHAIR.name]


def test_top_customers_are_ranked_over_the_period_the_model_chose() -> None:
    call = ToolCall(
        id="c1",
        name="sales_summary",
        arguments=json.dumps(
            {"date_from": "2026-10-01", "date_to": "2026-10-31", "group_by": "partner"}
        ),
    )
    turn = AgentTurn(answer="", stop_reason="answered", tool_calls=(call,), messages=())

    assert _resolver()(Fact(fact="top_customers"), turn) == ["Acme Corporation"]


def test_top_customers_need_a_sales_summary_by_partner() -> None:
    with pytest.raises(FactError, match="sales_summary"):
        _resolver()(Fact(fact="top_customers"), NO_TURN)


def test_lookup_facts() -> None:
    resolve = _resolver()

    assert resolve(Fact(fact="partner", partner="Azure Interior", field="city"), NO_TURN) == [
        "Fremont"
    ]
    assert resolve(Fact(fact="order", order="S1", field="partner_name"), NO_TURN) == [
        "Acme Corporation"
    ]
    assert resolve(Fact(fact="order", order="S1", field="amount_total"), NO_TURN) == 300.0
    assert resolve(Fact(fact="order", order="S1", field="line_products"), NO_TURN) == [CHAIR.name]
    assert (
        resolve(Fact(fact="product", product="Office Chair", field="list_price"), NO_TURN) == 70.0
    )
    assert resolve(Fact(fact="product", product="Office Chair", field="default_code"), NO_TURN) == [
        "FURN_7777"
    ]


def test_unknown_records_are_fact_errors() -> None:
    resolve = _resolver()

    with pytest.raises(FactError, match="Nobody"):
        resolve(Fact(fact="partner", partner="Nobody", field="city"), NO_TURN)
    with pytest.raises(FactError, match="S404"):
        resolve(Fact(fact="order", order="S404", field="partner_name"), NO_TURN)
