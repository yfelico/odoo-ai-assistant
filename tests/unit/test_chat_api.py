import json
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake_erp import FakeERP
from app.adapters.fake_llm import FakeLLM
from app.agent.conversations import ConversationStore
from app.agent.loop import Agent
from app.api.routes import get_agent, get_conversation_store
from app.domain.models import ChatMessage, Partner, ToolCall
from app.main import app

ACME = Partner(id=1, name="Acme Corp", email=None, city="Springfield")


def _assistant(content: str = "", *calls: ToolCall) -> ChatMessage:
    return ChatMessage(role="assistant", content=content, tool_calls=calls)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM([])


@pytest.fixture
def client(llm: FakeLLM) -> Iterator[TestClient]:
    agent = Agent(
        llm=llm,
        gateway=FakeERP(partners=[ACME], orders=[]),
        max_iterations=6,
        timeout_seconds=60,
        today=lambda: date(2026, 11, 18),
    )
    store = ConversationStore(ttl_seconds=3600)
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_conversation_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chat_answers_and_starts_a_conversation(client: TestClient, llm: FakeLLM) -> None:
    llm.replies.append(_assistant("Hola, ¿en qué te ayudo?"))

    response = client.post("/chat", json={"message": "hola"})

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]
    assert body["answer"] == "Hola, ¿en qué te ayudo?"
    assert body["pending_draft"] is None
    assert body["tool_calls"] == []


def test_chat_lists_the_tools_used(client: TestClient, llm: FakeLLM) -> None:
    call = ToolCall(id="c1", name="search_partners", arguments=json.dumps({"name": "acme"}))
    llm.replies.extend([_assistant("", call), _assistant("Acme Corp, en Springfield.")])

    response = client.post("/chat", json={"message": "¿Dónde está Acme?"})

    assert response.json()["tool_calls"] == [
        {"name": "search_partners", "arguments": {"name": "acme"}}
    ]


def test_chat_shows_unparseable_arguments_as_text(client: TestClient, llm: FakeLLM) -> None:
    call = ToolCall(id="c1", name="search_partners", arguments="{broken")
    llm.replies.extend([_assistant("", call), _assistant("Perdona, repito.")])

    response = client.post("/chat", json={"message": "hola"})

    assert response.json()["tool_calls"] == [{"name": "search_partners", "arguments": "{broken"}]


def test_chat_continues_an_existing_conversation(client: TestClient, llm: FakeLLM) -> None:
    llm.replies.extend([_assistant("Hola."), _assistant("Sigo aquí.")])
    first = client.post("/chat", json={"message": "hola"}).json()

    second = client.post(
        "/chat", json={"message": "¿sigues?", "conversation_id": first["conversation_id"]}
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    sent = [(m.role, m.content) for m in llm.requests[1].messages[1:]]
    assert sent == [("user", "hola"), ("assistant", "Hola."), ("user", "¿sigues?")]


def test_chat_rejects_an_empty_message(client: TestClient) -> None:
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_chat_reports_model_failure_as_service_unavailable(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "hola"})  # empty script: LLMError

    assert response.status_code == 503
    assert response.json()["detail"]


def test_root_serves_the_chat_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/chat" in response.text
