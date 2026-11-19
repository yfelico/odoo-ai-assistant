"""Every case of ``cases.yaml``, answered by the real model over the Odoo demo data.

Requires Odoo running (``./scripts/init_odoo.sh``) and ``LLM_API_KEY`` in .env. Select a
subset with ``-k``, e.g. ``pytest -m evals -k ventas``.
"""

import pytest

from app.config import Settings
from tests.evals.cases import Case, load_cases
from tests.evals.conftest import DEFAULT_MODEL
from tests.evals.report import EvalResult
from tests.evals.runner import EvalRunner

pytestmark = pytest.mark.evals

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[f"{case.category}-{case.id}" for case in CASES])
def test_case(
    case: Case, model: str, runner: EvalRunner, settings: Settings, results: list[EvalResult]
) -> None:
    result = runner.run(case, settings.llm_model if model == DEFAULT_MODEL else model)
    results.append(result)
    assert result.passed, "; ".join(result.failures) + f"\nAnswer: {result.answer}"
