"""Ports the application depends on. Adapters in app/adapters implement them."""

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from app.domain.models import (
    ChatMessage,
    CreatedQuotation,
    GroupBy,
    LowStockItem,
    OverdueInvoice,
    Partner,
    Product,
    QuotationLine,
    SaleOrderDetail,
    SalesSummary,
    ToolSpec,
)


class ERPGateway(Protocol):
    """Business-level access to the ERP. The only way the app talks to Odoo."""

    def search_partners(self, name: str, limit: int) -> list[Partner]:
        """Partners whose name contains ``name`` (case-insensitive)."""
        ...

    def get_partner(self, partner_id: int) -> Partner | None:
        """The partner with that id, if it exists."""
        ...

    def search_products(self, name: str, limit: int) -> list[Product]:
        """Saleable products whose name or internal reference contains ``name``."""
        ...

    def get_products(self, product_ids: Iterable[int]) -> list[Product]:
        """The saleable products among ``product_ids``; unknown ids are left out."""
        ...

    def sales_summary(
        self, date_from: date, date_to: date, group_by: GroupBy, limit: int
    ) -> SalesSummary:
        """Confirmed sales between both dates (inclusive, UTC days).

        Groups are sorted by total descending and capped at ``limit``; the
        overall total and order count always cover the whole period.
        """
        ...

    def low_stock_products(self, limit: int) -> list[LowStockItem]:
        """Replenishment rules with on-hand stock below their minimum.

        Sorted by shortfall (minimum minus on hand) descending.
        """
        ...

    def overdue_invoices(self, partner_id: int | None, as_of: date) -> list[OverdueInvoice]:
        """Posted, unpaid customer invoices due before ``as_of``, oldest due first."""
        ...

    def get_sale_order(self, name: str) -> SaleOrderDetail | None:
        """The order with that exact name (case-insensitive) and its product lines."""
        ...

    def create_quotation(
        self, partner_id: int, lines: Sequence[QuotationLine], note: str
    ) -> CreatedQuotation:
        """Create a draft sale order priced by the ERP and log ``note`` on it.

        Only called after the user confirms a draft; the agent never reaches it.
        """
        ...

    def ping(self) -> bool:
        """Whether the ERP answers. Never raises."""
        ...


class LLMClient(Protocol):
    """A chat model that can request tool calls."""

    def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], timeout: float
    ) -> ChatMessage:
        """The assistant's next message. Raises ``LLMError`` on provider failure."""
        ...
