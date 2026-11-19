"""Business tools the model may call: input validation, dispatch and result wrapping.

The model gets the read tools plus ``prepare_quotation``, which only stores a draft.
``create_quotation`` is deliberately not a tool: only ``POST /confirm`` calls it, after
the user approves the draft.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_json

from app.agent import audit
from app.agent.drafts import DEFAULT_DRAFT_TTL_SECONDS, DraftStore
from app.domain.models import (
    CreatedQuotation,
    ERPError,
    ERPUnavailableError,
    GroupBy,
    QuotationDraft,
    QuotationLine,
    ToolCall,
    ToolSpec,
)
from app.domain.ports import ERPGateway

logger = logging.getLogger(__name__)

DATA_OPEN = "<odoo_data>"
DATA_CLOSE = "</odoo_data>"
QUOTATION_NOTE = "Creado por el asistente de IA tras confirmación del usuario"

SearchText = Annotated[str, Field(min_length=1, max_length=100)]
SearchLimit = Annotated[int, Field(ge=1, le=20, description="Maximum results (1-20).")]


def _default_drafts() -> DraftStore:
    return DraftStore(ttl_seconds=DEFAULT_DRAFT_TTL_SECONDS)


@dataclass(frozen=True, slots=True)
class ToolContext:
    gateway: ERPGateway
    today: date
    drafts: DraftStore = field(default_factory=_default_drafts)
    conversation_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool
    draft: QuotationDraft | None = None  # set when prepare_quotation stored a draft


class ToolFailedError(Exception):
    """A tool ran but could not answer; the message is shown to the model."""


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchPartnersInput(_Input):
    name: SearchText = Field(description="Fragment of the customer or contact name.")
    limit: SearchLimit = 5


class SearchProductsInput(_Input):
    name: SearchText = Field(description="Fragment of the product name or internal reference.")
    limit: SearchLimit = 5


class SalesSummaryInput(_Input):
    date_from: date = Field(description="First day of the period (inclusive).")
    date_to: date = Field(description="Last day of the period (inclusive).")
    group_by: GroupBy = Field(default="none", description="Break the total down by this key.")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum groups (1-50).")

    @model_validator(mode="after")
    def _check_period(self) -> Self:
        if self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class LowStockProductsInput(_Input):
    limit: int = Field(default=20, ge=1, le=50, description="Maximum products (1-50).")


class OverdueInvoicesInput(_Input):
    partner_id: int | None = Field(
        default=None, gt=0, description="Only this customer's invoices (id from search_partners)."
    )


class GetSaleOrderInput(_Input):
    name: str = Field(min_length=1, max_length=64, description="Order reference, e.g. S00023.")


class QuotationLineInput(_Input):
    product_id: int = Field(gt=0, description="Product id from search_products.")
    quantity: float = Field(gt=0, le=100_000, description="Quantity in the product's unit.")


class PrepareQuotationInput(_Input):
    partner_id: int = Field(gt=0, description="Customer id from search_partners.")
    lines: list[QuotationLineInput] = Field(min_length=1, max_length=50)


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, object]
    execute: Callable[[ToolContext, str], object]


def _tool[T: BaseModel](
    name: str,
    description: str,
    input_model: type[T],
    handler: Callable[[ToolContext, T], object],
) -> Tool:
    def execute(context: ToolContext, arguments: str) -> object:
        return handler(context, input_model.model_validate_json(arguments or "{}"))

    return Tool(name, description, input_model.model_json_schema(), execute)


def _search_partners(ctx: ToolContext, args: SearchPartnersInput) -> object:
    return ctx.gateway.search_partners(args.name, args.limit)


def _search_products(ctx: ToolContext, args: SearchProductsInput) -> object:
    return ctx.gateway.search_products(args.name, args.limit)


def _sales_summary(ctx: ToolContext, args: SalesSummaryInput) -> object:
    return ctx.gateway.sales_summary(args.date_from, args.date_to, args.group_by, args.limit)


def _low_stock_products(ctx: ToolContext, args: LowStockProductsInput) -> object:
    return ctx.gateway.low_stock_products(args.limit)


def _overdue_invoices(ctx: ToolContext, args: OverdueInvoicesInput) -> object:
    invoices = ctx.gateway.overdue_invoices(args.partner_id, ctx.today)
    return {
        "as_of": ctx.today,
        "count": len(invoices),
        "total_residual": round(sum(inv.amount_residual for inv in invoices), 2),
        "invoices": invoices,
    }


def _get_sale_order(ctx: ToolContext, args: GetSaleOrderInput) -> object:
    order = ctx.gateway.get_sale_order(args.name)
    if order is None:
        raise ToolFailedError(f"No sale order named {args.name!r}.")
    return order


def _prepare_quotation(ctx: ToolContext, args: PrepareQuotationInput) -> object:
    partner = ctx.gateway.get_partner(args.partner_id)
    if partner is None:
        raise ToolFailedError(f"No customer with id {args.partner_id}.")
    wanted = {line.product_id for line in args.lines}
    products = {p.id: p for p in ctx.gateway.get_products(sorted(wanted))}
    missing = sorted(wanted - products.keys())
    if missing:
        raise ToolFailedError(f"Products not found or not for sale: {missing}.")
    lines = [
        QuotationLine(
            product_id=line.product_id,
            product_name=products[line.product_id].name,
            quantity=line.quantity,
            price_unit=products[line.product_id].list_price,
            subtotal=round(line.quantity * products[line.product_id].list_price, 2),
        )
        for line in args.lines
    ]
    return ctx.drafts.create(partner_id=partner.id, partner_name=partner.name, lines=lines)


READ_TOOLS: Mapping[str, Tool] = {
    tool.name: tool
    for tool in (
        _tool(
            "search_partners",
            "Find customers or contacts by name. Use it to get a partner_id.",
            SearchPartnersInput,
            _search_partners,
        ),
        _tool(
            "search_products",
            "Find saleable products by name or internal reference, with sale price and unit.",
            SearchProductsInput,
            _search_products,
        ),
        _tool(
            "sales_summary",
            "Untaxed total and number of confirmed sale orders in a period (UTC days, "
            "inclusive), optionally grouped by customer or product and sorted by total.",
            SalesSummaryInput,
            _sales_summary,
        ),
        _tool(
            "low_stock_products",
            "Products whose on-hand stock is below the minimum of their replenishment rule.",
            LowStockProductsInput,
            _low_stock_products,
        ),
        _tool(
            "overdue_invoices",
            "Posted, unpaid customer invoices past their due date, with the total balance due.",
            OverdueInvoicesInput,
            _overdue_invoices,
        ),
        _tool(
            "get_sale_order",
            "Header and product lines of one sale order or quotation by its reference.",
            GetSaleOrderInput,
            _get_sale_order,
        ),
    )
}


AGENT_TOOLS: Mapping[str, Tool] = {
    **READ_TOOLS,
    "prepare_quotation": _tool(
        "prepare_quotation",
        "Prepare a quotation draft for a customer with product lines, priced at list price. "
        "It does NOT create anything in Odoo: the user must confirm the draft in the "
        "interface. Get partner_id from search_partners and product_id from search_products.",
        PrepareQuotationInput,
        _prepare_quotation,
    ),
}


def tool_specs(tools: Mapping[str, Tool]) -> list[ToolSpec]:
    return [ToolSpec(t.name, t.description, t.parameters) for t in tools.values()]


def run_tool(tools: Mapping[str, Tool], call: ToolCall, context: ToolContext) -> ToolResult:
    """Run one tool call. Never raises: failures become readable errors for the model."""
    tool = tools.get(call.name)
    if tool is None:
        return _error(f"Unknown tool {call.name!r}. Available tools: {', '.join(tools)}.")
    try:
        value = tool.execute(context, call.arguments)
    except (ValidationError, ToolFailedError, ERPError) as err:
        return _error(_failure_message(call.name, err))
    if isinstance(value, QuotationDraft):
        return ToolResult(content=_wrap(_draft_view(value)), is_error=False, draft=value)
    return ToolResult(content=_wrap(value), is_error=False)


def _failure_message(tool: str, err: Exception) -> str:
    if isinstance(err, ValidationError):
        return f"Invalid arguments for {tool}: {_describe(err)}"
    if isinstance(err, ERPUnavailableError):
        logger.warning("Tool %s failed: Odoo unreachable", tool)
        return "Odoo is unreachable right now. Try again later."
    if isinstance(err, ERPError):
        logger.warning("Tool %s failed: %s", tool, err)
        return f"Odoo could not answer: {err}"
    return str(err)


def create_quotation(
    draft_id: str, drafts: DraftStore, gateway: ERPGateway, confirmed_by: int | None = None
) -> CreatedQuotation:
    """Create the confirmed draft in the ERP. Only ``POST /confirm`` calls this.

    The draft is consumed so it cannot be confirmed twice; if the ERP fails it is
    restored and the user can retry. ``confirmed_by`` is the Odoo user id that
    confirmed it, when known; it goes to the chatter note and the audit trail.
    """
    draft = drafts.take(draft_id)
    note = QUOTATION_NOTE if confirmed_by is None else f"{QUOTATION_NOTE} #{confirmed_by}"
    started = time.monotonic()
    try:
        created = gateway.create_quotation(draft.partner_id, draft.lines, note)
    except ERPError:
        drafts.restore(draft)
        _audit_creation(draft, None, started, confirmed_by)
        raise
    _audit_creation(draft, created.id, started, confirmed_by)
    return created


def _audit_creation(
    draft: QuotationDraft, quotation_id: int | None, started: float, confirmed_by: int | None
) -> None:
    audit.quotation_created(
        draft_id=draft.id,
        partner_id=draft.partner_id,
        quotation_id=quotation_id,
        outcome="error" if quotation_id is None else "ok",
        duration=time.monotonic() - started,
        confirmed_by_user_id=confirmed_by,
    )


def _draft_view(draft: QuotationDraft) -> dict[str, object]:
    return {
        "draft_id": draft.id,
        "status": "pending_user_confirmation",
        "partner": {"id": draft.partner_id, "name": draft.partner_name},
        "lines": draft.lines,
        "estimated_total_untaxed": draft.estimated_total_untaxed,
        "note": "Not created yet: the user must confirm this draft in the interface. "
        "Odoo sets the final prices and taxes on creation.",
    }


def _wrap(value: object) -> str:
    # Escaping "<" keeps Odoo text from closing the tag; JSON decoding restores it.
    payload = to_json(value).decode().replace("<", "\\u003c")
    return f"{DATA_OPEN}{payload}{DATA_CLOSE}"


def _describe(err: ValidationError) -> str:
    # Input values are left out: they may echo customer data.
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in err.errors(include_input=False)
    )


def _error(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)
