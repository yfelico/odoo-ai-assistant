"""Business tools the model may call: input validation, dispatch and result wrapping.

Only read operations are registered here. Quotation creation is deliberately absent:
it is triggered by ``POST /confirm`` after the user approves a draft.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_json

from app.domain.models import ERPError, ERPUnavailableError, GroupBy, ToolCall, ToolSpec
from app.domain.ports import ERPGateway

logger = logging.getLogger(__name__)

DATA_OPEN = "<odoo_data>"
DATA_CLOSE = "</odoo_data>"

SearchText = Annotated[str, Field(min_length=1, max_length=100)]
SearchLimit = Annotated[int, Field(ge=1, le=20, description="Maximum results (1-20).")]


@dataclass(frozen=True, slots=True)
class ToolContext:
    gateway: ERPGateway
    today: date


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool


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


def tool_specs(tools: Mapping[str, Tool]) -> list[ToolSpec]:
    return [ToolSpec(t.name, t.description, t.parameters) for t in tools.values()]


def run_tool(tools: Mapping[str, Tool], call: ToolCall, context: ToolContext) -> ToolResult:
    """Run one tool call. Never raises: failures become readable errors for the model."""
    tool = tools.get(call.name)
    if tool is None:
        return _error(f"Unknown tool {call.name!r}. Available tools: {', '.join(tools)}.")
    try:
        value = tool.execute(context, call.arguments)
    except ValidationError as err:
        return _error(f"Invalid arguments for {call.name}: {_describe(err)}")
    except ToolFailedError as err:
        return _error(str(err))
    except ERPUnavailableError:
        logger.warning("Tool %s failed: Odoo unreachable", call.name)
        return _error("Odoo is unreachable right now. Try again later.")
    except ERPError as err:
        logger.warning("Tool %s failed: %s", call.name, err)
        return _error(f"Odoo could not answer: {err}")
    return ToolResult(content=_wrap(value), is_error=False)


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
