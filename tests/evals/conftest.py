"""Eval run configuration and report.

``EVAL_MODELS`` (comma-separated LiteLLM model strings) selects the models to compare;
by default the configured ``LLM_MODEL``. ``EVAL_REQUESTS_PER_MINUTE`` keeps each model
under its provider quota. Both are read from the environment or .env. When the session
ends, a comparison report is written to ``tests/evals/reports/``.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import Settings
from tests.evals.report import EvalResult, write_report
from tests.evals.runner import EvalRunner

DEFAULT_MODEL = "default"
REPORTS_DIR = Path(__file__).with_name("reports")
RESULTS: list[EvalResult] = []


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    eval_models: str = ""
    eval_requests_per_minute: int = Field(default=5, ge=1)

    def models(self) -> list[str]:
        return [model.strip() for model in self.eval_models.split(",") if model.strip()]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "model" in metafunc.fixturenames:
        # Resolved to LLM_MODEL by the ``runner`` fixture, which needs the app settings.
        models = EvalSettings().models() or [DEFAULT_MODEL]
        metafunc.parametrize("model", models, ids=models)


@pytest.fixture(scope="session")
def settings() -> Settings:
    settings = Settings()
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY is not set in .env")
    return settings


@pytest.fixture(scope="session")
def runner(settings: Settings) -> EvalRunner:
    return EvalRunner(settings, EvalSettings().eval_requests_per_minute)


@pytest.fixture
def results() -> list[EvalResult]:
    return RESULTS


def pytest_sessionfinish(session: pytest.Session) -> None:
    if RESULTS:
        path = write_report(RESULTS, REPORTS_DIR, datetime.now(UTC))
        terminal = session.config.pluginmanager.get_plugin("terminalreporter")
        if terminal is not None:
            terminal.write_line(f"Eval report: {path}")
