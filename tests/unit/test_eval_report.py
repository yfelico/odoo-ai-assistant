import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.models import Usage
from tests.evals.report import EvalResult, render_markdown, summarise, write_report

NOW = datetime(2026, 11, 18, 21, 30, tzinfo=UTC)


# Keyword-only overrides keep each test's result readable.
def _result(  # noqa: PLR0913
    model: str,
    case_id: str,
    *,
    passed: bool = True,
    category: str = "ventas",
    latency: float = 1.0,
    cost: float | None = 0.001,
) -> EvalResult:
    return EvalResult(
        model=model,
        case_id=case_id,
        category=category,
        passed=passed,
        failures=() if passed else ("missing tool sales_summary",),
        latency_seconds=latency,
        usage=Usage(input_tokens=1000, output_tokens=100, cost_usd=cost),
        tools=("sales_summary",),
        answer="Respuesta",
    )


RESULTS = [
    _result("gemini/a", "c1", latency=1.0),
    _result("gemini/a", "c2", passed=False, category="pedidos", latency=3.0),
    _result("gemini/b", "c1", latency=2.0, cost=None),
]


def test_summarises_accuracy_latency_tokens_and_cost_per_model() -> None:
    first, second = summarise(RESULTS)

    assert (first.model, first.cases, first.passed) == ("gemini/a", 2, 1)
    assert first.accuracy == 0.5
    assert first.by_category == {"pedidos": (0, 1), "ventas": (1, 1)}
    assert first.mean_latency_seconds == 2.0
    assert first.p95_latency_seconds == 3.0
    assert (first.input_tokens, first.output_tokens) == (2000, 200)
    assert first.cost_usd == pytest.approx(0.002)
    assert first.cost_per_case_usd == pytest.approx(0.001)
    assert second.model == "gemini/b"
    assert second.cost_usd is None


def test_markdown_compares_models_and_lists_failures() -> None:
    text = render_markdown(RESULTS, NOW)

    assert "| gemini/a | 1/2 (50%) |" in text
    assert "| gemini/b | 1/1 (100%) |" in text
    assert "n/d" in text
    assert "c2" in text
    assert "missing tool sales_summary" in text


def test_writes_markdown_and_json(tmp_path: Path) -> None:
    markdown = write_report(RESULTS, tmp_path, NOW)

    assert markdown == tmp_path / "evals-20261118-213000.md"
    data = json.loads(markdown.with_suffix(".json").read_text(encoding="utf-8"))
    assert [row["case_id"] for row in data["results"]] == ["c1", "c2", "c1"]
    assert data["summary"][0]["accuracy"] == 0.5
