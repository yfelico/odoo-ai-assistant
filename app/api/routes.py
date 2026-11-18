import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, JsonValue

from app.adapters.litellm_client import LiteLLMClient
from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.agent.conversations import ConversationStore
from app.agent.loop import Agent
from app.config import Settings
from app.domain.models import LLMError, ToolCall
from app.domain.ports import ERPGateway

logger = logging.getLogger(__name__)

router = APIRouter()

CHAT_PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_erp_gateway() -> ERPGateway:
    return OdooXmlRpcGateway.from_settings(get_settings())


@lru_cache
def get_agent() -> Agent:
    settings = get_settings()
    return Agent(
        llm=LiteLLMClient.from_settings(settings),
        gateway=get_erp_gateway(),
        max_iterations=settings.agent_max_iterations,
        timeout_seconds=settings.agent_timeout_seconds,
    )


@lru_cache
def get_conversation_store() -> ConversationStore:
    return ConversationStore(ttl_seconds=get_settings().conversation_ttl_seconds)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    odoo: Literal["ok", "unreachable"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class ToolCallView(BaseModel):
    name: str
    # Parsed JSON when valid; otherwise the raw text the model produced.
    arguments: JsonValue


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    pending_draft: None = None  # quotation drafts arrive in phase 3
    tool_calls: list[ToolCallView]


@router.get("/", include_in_schema=False)
def chat_page() -> FileResponse:
    return FileResponse(CHAT_PAGE, media_type="text/html")


@router.get("/health")
def health(gateway: Annotated[ERPGateway, Depends(get_erp_gateway)]) -> HealthResponse:
    return HealthResponse(odoo="ok" if gateway.ping() else "unreachable")


@router.post(
    "/chat",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The model failed"}},
)
def chat(
    request: ChatRequest,
    agent: Annotated[Agent, Depends(get_agent)],
    store: Annotated[ConversationStore, Depends(get_conversation_store)],
) -> ChatResponse:
    conversation_id = request.conversation_id or store.new_id()
    try:
        turn = agent.run(store.history(conversation_id), request.message)
    except LLMError as err:
        logger.warning("Conversation %s: model failed: %s", conversation_id, err)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The language model is not available right now. Try again later.",
        ) from err
    store.save(conversation_id, turn.messages)
    return ChatResponse(
        conversation_id=conversation_id,
        answer=turn.answer,
        tool_calls=[_view(call) for call in turn.tool_calls],
    )


def _view(call: ToolCall) -> ToolCallView:
    try:
        arguments: JsonValue = json.loads(call.arguments)
    except json.JSONDecodeError:
        arguments = call.arguments
    return ToolCallView(name=call.name, arguments=arguments)
