"""Business entities, LLM message types and errors. Pure Python: no external dependencies."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

GroupBy = Literal["none", "partner", "product"]
Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class Partner:
    id: int
    name: str
    email: str | None
    city: str | None


@dataclass(frozen=True, slots=True)
class SalesGroup:
    """Sales of one partner or product. Amounts are untaxed, in company currency."""

    key_id: int
    key_name: str
    total_untaxed: float
    order_count: int


@dataclass(frozen=True, slots=True)
class SalesSummary:
    date_from: date
    date_to: date
    group_by: GroupBy
    total_untaxed: float
    order_count: int
    groups: tuple[SalesGroup, ...]


@dataclass(frozen=True, slots=True)
class Product:
    id: int
    name: str
    default_code: str | None
    list_price: float
    uom: str


@dataclass(frozen=True, slots=True)
class LowStockItem:
    """A replenishment rule whose on-hand quantity is below its minimum."""

    product_id: int
    product_name: str
    qty_on_hand: float
    qty_forecast: float
    min_qty: float


@dataclass(frozen=True, slots=True)
class OverdueInvoice:
    id: int
    name: str
    partner_id: int
    partner_name: str
    due_date: date
    amount_residual: float
    amount_total: float


@dataclass(frozen=True, slots=True)
class SaleOrderLine:
    product_id: int
    product_name: str
    description: str
    quantity: float
    price_unit: float
    subtotal: float


@dataclass(frozen=True, slots=True)
class SaleOrderDetail:
    id: int
    name: str
    partner_id: int
    partner_name: str
    date_order: datetime
    state: str
    amount_untaxed: float
    amount_total: float
    lines: tuple[SaleOrderLine, ...]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model; ``arguments`` is raw JSON text."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    # Opaque provider message (e.g. reasoning signatures) that must be sent back unchanged.
    provider_payload: Mapping[str, object] | None = None


class ERPError(Exception):
    """The ERP rejected or failed to process a request."""


class ERPUnavailableError(ERPError):
    """The ERP could not be reached."""


class ERPAuthenticationError(ERPError):
    """The ERP rejected the configured credentials."""


class LLMError(Exception):
    """The language model provider failed to produce a reply."""
