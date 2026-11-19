from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.adapters.fake_erp import FakeERP
from app.adapters.fake_llm import FakeLLM
from app.agent.conversations import ConversationStore
from app.agent.drafts import DraftStore
from app.agent.loop import Agent
from app.api.routes import (
    get_agent,
    get_conversation_store,
    get_draft_store,
    get_erp_gateway,
    get_settings,
)
from app.config import Settings
from app.domain.models import ChatMessage
from app.main import app

# Test-only value, not a real credential.
TOKEN = "s3cret-token"  # noqa: S105


def _client(api_token: str | None) -> TestClient:
    erp = FakeERP(partners=[], orders=[])
    drafts = DraftStore(ttl_seconds=900)
    agent = Agent(
        llm=FakeLLM([ChatMessage(role="assistant", content="Hola")]),
        gateway=erp,
        max_iterations=6,
        timeout_seconds=60,
        today=lambda: date(2026, 11, 19),
        drafts=drafts,
    )
    settings = Settings(
        odoo_api_key=SecretStr("unused"),
        api_token=SecretStr(api_token) if api_token else None,
    )
    conversations = ConversationStore(ttl_seconds=3600)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_conversation_store] = lambda: conversations
    app.dependency_overrides[get_draft_store] = lambda: drafts
    app.dependency_overrides[get_erp_gateway] = lambda: erp
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_without_a_configured_token_the_api_is_open() -> None:
    response = _client(None).post("/chat", json={"message": "hola"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}],
    ids=["missing", "wrong", "not-bearer"],
)
@pytest.mark.parametrize(
    ("path", "body"),
    [("/chat", {"message": "hola"}), ("/confirm", {"draft_id": "d1"})],
)
def test_with_a_token_chat_and_confirm_require_it(
    path: str, body: dict[str, str], headers: dict[str, str]
) -> None:
    response = _client(TOKEN).post(path, json=body, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_with_the_right_token_requests_go_through() -> None:
    response = _client(TOKEN).post(
        "/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "Hola"


def test_health_stays_open_for_monitoring() -> None:
    response = _client(TOKEN).get("/health")

    assert response.status_code == 200
