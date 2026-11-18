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
from app.agent.drafts import DraftNotFoundError, DraftStore
from app.agent.loop import Agent
from app.agent.tools import create_quotation
from app.config import Settings
from app.domain.models import (
    ERPError,
    ERPUnavailableError,
    LLMError,
    QuotationDraft,
    ToolCall,
)
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
def get_draft_store() -> DraftStore:
    return DraftStore(ttl_seconds=get_settings().draft_ttl_seconds)


@lru_cache
def get_agent() -> Agent:
    settings = get_settings()
    return Agent(
        llm=LiteLLMClient.from_settings(settings),
        gateway=get_erp_gateway(),
        max_iterations=settings.agent_max_iterations,
        timeout_seconds=settings.agent_timeout_seconds,
        drafts=get_draft_store(),
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


class DraftLineView(BaseModel):
    product_id: int
    product_name: str
    quantity: float
    price_unit: float
    subtotal: float


class DraftView(BaseModel):
    draft_id: str
    partner_id: int
    partner_name: str
    lines: list[DraftLineView]
    estimated_total_untaxed: float

    @classmethod
    def of(cls, draft: QuotationDraft) -> "DraftView":
        return cls(
            draft_id=draft.id,
            partner_id=draft.partner_id,
            partner_name=draft.partner_name,
            lines=[
                DraftLineView(
                    product_id=line.product_id,
                    product_name=line.product_name,
                    quantity=line.quantity,
                    price_unit=line.price_unit,
                    subtotal=line.subtotal,
                )
                for line in draft.lines
            ],
            estimated_total_untaxed=draft.estimated_total_untaxed,
        )


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    pending_draft: DraftView | None = None
    tool_calls: list[ToolCallView]


class ConfirmRequest(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)


class ConfirmResponse(BaseModel):
    quotation_id: int
    name: str
    amount_untaxed: float
    amount_total: float


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
        turn = agent.run(store.history(conversation_id), request.message, conversation_id)
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
        pending_draft=DraftView.of(turn.pending_draft) if turn.pending_draft else None,
        tool_calls=[_view(call) for call in turn.tool_calls],
    )


@router.post(
    "/confirm",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Unknown, expired or already used draft"},
        status.HTTP_502_BAD_GATEWAY: {"description": "Odoo rejected the quotation"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Odoo is unreachable"},
    },
)
def confirm(
    request: ConfirmRequest,
    drafts: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[ERPGateway, Depends(get_erp_gateway)],
) -> ConfirmResponse:
    try:
        created = create_quotation(request.draft_id, drafts, gateway)
    except DraftNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The draft does not exist, has expired or was already confirmed. "
            "Ask the assistant to prepare it again.",
        ) from err
    except ERPUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Odoo is not reachable right now. The draft is kept: try again later.",
        ) from err
    except ERPError as err:
        logger.warning("Draft %s: Odoo rejected the quotation: %s", request.draft_id, err)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Odoo rejected the quotation. The draft is kept.",
        ) from err
    return ConfirmResponse(
        quotation_id=created.id,
        name=created.name,
        amount_untaxed=created.amount_untaxed,
        amount_total=created.amount_total,
    )


def _view(call: ToolCall) -> ToolCallView:
    try:
        arguments: JsonValue = json.loads(call.arguments)
    except json.JSONDecodeError:
        arguments = call.arguments
    return ToolCallView(name=call.name, arguments=arguments)
