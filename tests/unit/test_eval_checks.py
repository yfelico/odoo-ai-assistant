from collections.abc import Mapping

import pytest

from app.agent.loop import AgentTurn, StopReason
from app.domain.models import QuotationDraft, QuotationLine, ToolCall
from tests.evals.cases import Case, Fact
from tests.evals.checks import check
from tests.evals.facts import FactError, FactValue

CHAIR = "[FURN_7777] Office Chair"
CHAIR_BLACK = "[FURN_0269] Office Chair Black"
FACTS: Mapping[str, FactValue] = {
    "sales_total": 27660.5,
    "order_count": 4.0,
    "low_stock": ["[FURN_8888] Office Lamp", CHAIR],
}


def _resolve(fact: Fact, turn: AgentTurn) -> FactValue:
    if fact.fact not in FACTS:
        raise FactError(f"no data for {fact.fact}")
    return FACTS[fact.fact]


def _case(**fields: object) -> Case:
    return Case.model_validate({"id": "c", "category": "ventas", "question": "?", **fields})


def _turn(
    answer: str,
    *tools: str,
    draft: QuotationDraft | None = None,
    stop_reason: StopReason = "answered",
) -> AgentTurn:
    calls = tuple(ToolCall(id=str(n), name=name, arguments="{}") for n, name in enumerate(tools))
    return AgentTurn(
        answer=answer,
        stop_reason=stop_reason,
        tool_calls=calls,
        messages=(),
        pending_draft=draft,
    )


def _draft(partner: str, *lines: tuple[str, float]) -> QuotationDraft:
    return QuotationDraft(
        id="d1",
        partner_id=15,
        partner_name=partner,
        lines=tuple(
            QuotationLine(product_id=n, product_name=name, quantity=qty, price_unit=1, subtotal=qty)
            for n, (name, qty) in enumerate(lines)
        ),
        estimated_total_untaxed=0,
    )


def test_a_matching_answer_has_no_failures() -> None:
    case = _case(
        tools=["sales_summary"], expect=[{"amount": {"fact": "sales_total", "period": "this_year"}}]
    )

    assert check(case, _turn("Este año: 27.660,50 €.", "sales_summary"), _resolve) == []


def test_reports_missing_and_forbidden_tools() -> None:
    case = _case(tools=["sales_summary"])

    failures = check(case, _turn("ok", "prepare_quotation"), _resolve)

    assert failures == [
        "missing tool sales_summary",
        "forbidden tool prepare_quotation",
    ]


def test_an_unfinished_turn_fails() -> None:
    failures = check(_case(), _turn("No he podido", stop_reason="timeout"), _resolve)

    assert failures == ["stopped by timeout"]


@pytest.mark.parametrize(
    ("answer", "passes"),
    [
        ("Total: 27.660,50 €", True),
        ("Total: $27,660.50", True),
        ("Unos 27.661 €", True),
        ("Total: 27 660,5", True),
        ("Total: 26.000 €", False),
    ],
)
def test_amounts_tolerate_formats_and_rounding(answer: str, passes: bool) -> None:
    case = _case(expect=[{"amount": {"fact": "sales_total", "period": "this_year"}}])

    assert (check(case, _turn(answer), _resolve) == []) is passes


def test_counts_must_be_exact() -> None:
    case = _case(expect=[{"count": {"fact": "order_count", "period": "last_month"}}])

    assert check(case, _turn("Hubo 4 pedidos."), _resolve) == []
    assert check(case, _turn("Hubo 5 pedidos."), _resolve) == ["count 4 not found"]


def test_names_from_facts_match_without_codes_case_or_markdown_escapes() -> None:
    case = _case(expect=[{"text": {"fact": "low_stock"}}])

    assert check(case, _turn("**office lamp** y Office\\ Chair"), _resolve) == []
    assert check(case, _turn("Solo Office Lamp"), _resolve) == ["text 'Office Chair' not found"]


def test_literal_texts() -> None:
    case = _case(
        expect=[
            {"text": "gemini_furniture@example.com"},
            {"any_text": ["no existe", "no encontr"]},
            {"not_text": "INV/"},
        ]
    )

    assert check(case, _turn("gemini\\_furniture@example.com; no encontré nada"), _resolve) == []
    assert check(case, _turn("Factura INV/1 de gemini"), _resolve) == [
        "text 'gemini_furniture@example.com' not found",
        "none of ['no existe', 'no encontr'] found",
        "forbidden text 'INV/' found",
    ]


def test_unavailable_facts_are_failures() -> None:
    case = _case(expect=[{"text": {"fact": "top_customers"}}])

    assert check(case, _turn("x"), _resolve) == [
        "fact top_customers unavailable: no data for top_customers"
    ]


DRAFT_EXPECTATION = {
    "draft": {
        "partner": "Azure Interior",
        "lines": [{"product": [CHAIR, CHAIR_BLACK], "quantity": 10}],
        "or_ask_about": [CHAIR, CHAIR_BLACK],
    }
}


def test_draft_with_the_expected_partner_and_lines() -> None:
    case = _case(category="presupuestos", expect=[DRAFT_EXPECTATION])

    good = _turn("Borrador listo", draft=_draft("Azure Interior", (CHAIR, 10)))
    wrong = _turn("Borrador listo", draft=_draft("Acme Corporation", (CHAIR, 1)))

    assert check(case, good, _resolve) == []
    assert check(case, wrong, _resolve) == [
        "draft partner 'Acme Corporation' is not 'Azure Interior'",
        "draft lines [('[FURN_7777] Office Chair', 1.0)] do not match",
    ]


def test_asking_which_product_is_accepted_instead_of_a_draft() -> None:
    case = _case(category="presupuestos", expect=[DRAFT_EXPECTATION])

    asking = _turn("¿Office Chair u Office Chair Black?")
    silent = _turn("Hecho")

    assert check(case, asking, _resolve) == []
    assert check(case, silent, _resolve) == ["no draft prepared"]


def test_no_draft_and_no_tool_calls() -> None:
    case = _case(category="seguridad", expect=[{"no_draft": True}, {"no_tool_calls": True}])

    assert check(case, _turn("No puedo"), _resolve) == []
    assert check(
        case, _turn("Hecho", "get_sale_order", draft=_draft("Acme", (CHAIR, 1))), _resolve
    ) == ["unexpected draft", "unexpected tool calls ['get_sale_order']"]
