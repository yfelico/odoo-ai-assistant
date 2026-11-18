"""Business entities and errors. Pure Python: no external dependencies."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

GroupBy = Literal["none", "partner", "product"]


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


class ERPError(Exception):
    """The ERP rejected or failed to process a request."""


class ERPUnavailableError(ERPError):
    """The ERP could not be reached."""


class ERPAuthenticationError(ERPError):
    """The ERP rejected the configured credentials."""
