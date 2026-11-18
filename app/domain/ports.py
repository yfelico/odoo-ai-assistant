"""Ports the application depends on. Adapters in app/adapters implement them."""

from datetime import date
from typing import Protocol

from app.domain.models import GroupBy, Partner, SalesSummary


class ERPGateway(Protocol):
    """Business-level access to the ERP. The only way the app talks to Odoo."""

    def search_partners(self, name: str, limit: int) -> list[Partner]:
        """Partners whose name contains ``name`` (case-insensitive)."""
        ...

    def sales_summary(
        self, date_from: date, date_to: date, group_by: GroupBy, limit: int
    ) -> SalesSummary:
        """Confirmed sales between both dates (inclusive, UTC days).

        Groups are sorted by total descending and capped at ``limit``; the
        overall total and order count always cover the whole period.
        """
        ...

    def ping(self) -> bool:
        """Whether the ERP answers. Never raises."""
        ...
