import json
from datetime import date

import pytest

from app.adapters.fake_erp import FakeERP
from app.agent.drafts import DraftNotFoundError, DraftStore
from app.agent.tools import (
    AGENT_TOOLS,
    QUOTATION_NOTE,
    READ_TOOLS,
    ToolContext,
    ToolResult,
    create_quotation,
    run_tool,
)
from app.domain.models import (
    CreatedQuotation,
    ERPUnavailableError,
    Partner,
    Product,
    QuotationLine,
    ToolCall,
)

TODAY = date(2026, 11, 18)
AZURE = Partner(id=15, name="Azure Interior", email=None, city="Fremont")
CHAIR = Product(
    id=5, name="[FURN_7777] Office Chair", default_code="FURN_7777", list_price=70.0, uom="Units"
)
LAMP = Product(
    id=6, name="[FURN_8888] Office Lamp", default_code="FURN_8888", list_price=40.5, uom="Units"
)
ODOO_DATA_OPEN = "<odoo_data>"
ODOO_DATA_CLOSE = "</odoo_data>"


@pytest.fixture
def erp() -> FakeERP:
    return FakeERP(partners=[AZURE], orders=[], products=[CHAIR, LAMP])


@pytest.fixture
def drafts() -> DraftStore:
    return DraftStore(ttl_seconds=900)


@pytest.fixture
def context(erp: FakeERP, drafts: DraftStore) -> ToolContext:
    return ToolContext(gateway=erp, today=TODAY, drafts=drafts, conversation_id="conv-1")


def _prepare(context: ToolContext, partner_id: int, *lines: tuple[int, float]) -> ToolResult:
    arguments = {
        "partner_id": partner_id,
        "lines": [{"product_id": pid, "quantity": qty} for pid, qty in lines],
    }
    call = ToolCall(id="c1", name="prepare_quotation", arguments=json.dumps(arguments))
    return run_tool(AGENT_TOOLS, call, context)


def _data(result: ToolResult) -> dict[str, object]:
    assert not result.is_error, result.content
    data = json.loads(result.content.removeprefix(ODOO_DATA_OPEN).removesuffix(ODOO_DATA_CLOSE))
    assert isinstance(data, dict)
    return data


def test_agent_tools_are_the_read_tools_plus_prepare_quotation() -> None:
    assert set(AGENT_TOOLS) == {*READ_TOOLS, "prepare_quotation"}
    assert "create_quotation" not in AGENT_TOOLS


def test_prepare_quotation_stores_a_draft_with_list_prices(
    context: ToolContext, drafts: DraftStore
) -> None:
    result = _prepare(context, AZURE.id, (CHAIR.id, 10), (LAMP.id, 2))

    assert result.draft is not None
    assert drafts.get(result.draft.id) == result.draft
    assert result.draft.partner_name == "Azure Interior"
    assert result.draft.lines == (
        QuotationLine(
            product_id=5, product_name=CHAIR.name, quantity=10, price_unit=70.0, subtotal=700.0
        ),
        QuotationLine(
            product_id=6, product_name=LAMP.name, quantity=2, price_unit=40.5, subtotal=81.0
        ),
    )
    data = _data(result)
    assert data["draft_id"] == result.draft.id
    assert data["estimated_total_untaxed"] == pytest.approx(781.0)
    assert data["status"] == "pending_user_confirmation"


def test_prepare_quotation_writes_nothing_to_odoo(context: ToolContext, erp: FakeERP) -> None:
    _prepare(context, AZURE.id, (CHAIR.id, 10))

    assert erp.created_quotations == []


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [{"product_id": 5, "quantity": 0}],
        [{"product_id": 5, "quantity": -1}],
        [{"product_id": 5, "quantity": 1}] * 51,
    ],
    ids=["no-lines", "zero-quantity", "negative-quantity", "too-many-lines"],
)
def test_prepare_quotation_validates_lines(
    context: ToolContext, drafts: DraftStore, lines: list[dict[str, object]]
) -> None:
    call = ToolCall(
        id="c1",
        name="prepare_quotation",
        arguments=json.dumps({"partner_id": AZURE.id, "lines": lines}),
    )

    result = run_tool(AGENT_TOOLS, call, context)

    assert result.is_error
    assert "lines" in result.content
    assert result.draft is None


def test_prepare_quotation_rejects_an_unknown_customer(context: ToolContext) -> None:
    result = _prepare(context, 999, (CHAIR.id, 1))

    assert result.is_error
    assert "999" in result.content


def test_prepare_quotation_rejects_unknown_or_unsaleable_products(context: ToolContext) -> None:
    result = _prepare(context, AZURE.id, (CHAIR.id, 1), (77, 1))

    assert result.is_error
    assert "77" in result.content
    assert result.draft is None


def test_create_quotation_uses_the_draft_and_consumes_it(erp: FakeERP, drafts: DraftStore) -> None:
    draft = drafts.create(
        partner_id=AZURE.id,
        partner_name=AZURE.name,
        lines=(QuotationLine(5, CHAIR.name, 10, 70.0, 700.0),),
    )

    created = create_quotation(draft.id, drafts, erp)

    assert isinstance(created, CreatedQuotation)
    assert [(q.partner_id, q.lines, q.note) for q in erp.created_quotations] == [
        (AZURE.id, draft.lines, QUOTATION_NOTE)
    ]
    with pytest.raises(DraftNotFoundError):
        drafts.get(draft.id)


def test_create_quotation_with_unknown_draft_creates_nothing(
    erp: FakeERP, drafts: DraftStore
) -> None:
    with pytest.raises(DraftNotFoundError):
        create_quotation("missing", drafts, erp)

    assert erp.created_quotations == []


def test_create_quotation_keeps_the_draft_when_odoo_fails(drafts: DraftStore) -> None:
    class DownERP(FakeERP):
        def create_quotation(
            self, partner_id: int, lines: tuple[QuotationLine, ...], note: str
        ) -> CreatedQuotation:
            raise ERPUnavailableError("Odoo is unreachable")

    draft = drafts.create(
        partner_id=AZURE.id,
        partner_name=AZURE.name,
        lines=(QuotationLine(5, CHAIR.name, 1, 70.0, 70.0),),
    )

    with pytest.raises(ERPUnavailableError):
        create_quotation(draft.id, drafts, DownERP(partners=[], orders=[]))

    assert drafts.get(draft.id) == draft
