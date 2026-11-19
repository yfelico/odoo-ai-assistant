from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.evals.cases import CASES_PATH, Case, load_cases

MIN_CASES = 20


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_catalogue_loads_with_enough_unique_cases_in_every_category() -> None:
    cases = load_cases(CASES_PATH)

    assert len(cases) >= MIN_CASES
    assert len({case.id for case in cases}) == len(cases)
    assert {case.category for case in cases} == {
        "ventas",
        "inventario",
        "facturacion",
        "pedidos",
        "clientes",
        "presupuestos",
        "seguridad",
    }


def test_loads_a_case_with_its_expectations(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
- id: sales_this_month
  category: ventas
  question: "¿Cuánto vendimos este mes?"
  tools: [sales_summary]
  expect:
    - amount: {fact: sales_total, period: this_month}
    - text: "€"
""",
    )

    [case] = load_cases(path)

    assert case.id == "sales_this_month"
    assert case.tools == ["sales_summary"]
    assert len(case.expect) == 2


def test_read_only_cases_forbid_preparing_quotations_by_default() -> None:
    sales = Case(id="a", category="ventas", question="?")
    quotation = Case(id="b", category="presupuestos", question="?")
    explicit = Case(id="c", category="ventas", question="?", forbidden_tools=[])

    assert sales.effective_forbidden_tools == ["prepare_quotation"]
    assert quotation.effective_forbidden_tools == []
    assert explicit.effective_forbidden_tools == []


@pytest.mark.parametrize(
    "case",
    [
        "- {id: a, category: ventas, question: '?', tools: [run_sql]}",
        "- {id: a, category: ventas, question: '?', expect: [{amount: {fact: sales_total}}]}",
        "- {id: a, category: ventas, question: '?', expect: [{amount: {fact: magic}}]}",
        "- {id: a, category: ventas, question: '?', expect: [{guess: 1}]}",
        "- {id: a, category: cocina, question: '?'}",
        "- {id: a, category: ventas, question: '?', typo: 1}",
        "- {id: a, category: pedidos, question: '?',"
        " expect: [{text: {fact: order, order: S1, field: colour}}]}",
    ],
    ids=[
        "unknown-tool",
        "fact-missing-param",
        "unknown-fact",
        "unknown-expectation",
        "unknown-category",
        "unknown-key",
        "unknown-field",
    ],
)
def test_rejects_malformed_cases(tmp_path: Path, case: str) -> None:
    with pytest.raises(ValidationError):
        load_cases(_write(tmp_path, case))


def test_rejects_duplicated_ids(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "- {id: a, category: ventas, question: '?'}\n- {id: a, category: ventas, question: '?'}",
    )

    with pytest.raises(ValueError, match="Duplicated case id: a"):
        load_cases(path)
