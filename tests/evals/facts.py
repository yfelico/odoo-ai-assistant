"""Expected values of eval cases, computed live through the ERPGateway."""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from app.agent.loop import AgentTurn
from app.domain.models import (
    GroupBy,
    OverdueInvoice,
    Partner,
    Product,
    SaleOrderDetail,
    SalesSummary,
)
from app.domain.ports import ERPGateway
from tests.evals.cases import Fact, Period

type FactValue = float | list[str]

GROUP_LIMIT = 50
TOP_CUSTOMERS = 3
LOOKUP_LIMIT = 10
CODE_PREFIX = re.compile(r"^\[[^\]]+\]\s*")


class FactError(Exception):
    """A fact cannot be computed (e.g. its record does not exist)."""


def plain_name(name: str) -> str:
    """Drops the internal reference Odoo prefixes to product names (``[FURN_7777] ...``)."""
    return CODE_PREFIX.sub("", name)


def period_bounds(period: Period, today: date) -> tuple[date, date]:
    first_of_month = today.replace(day=1)
    if period == "this_month":
        next_month = (first_of_month + timedelta(days=32)).replace(day=1)
        return first_of_month, next_month - timedelta(days=1)
    if period == "last_month":
        last_day = first_of_month - timedelta(days=1)
        return last_day.replace(day=1), last_day
    return today.replace(month=1, day=1), today


@dataclass(frozen=True, slots=True)
class FactResolver:
    gateway: ERPGateway
    today: date

    def __call__(self, fact: Fact, turn: AgentTurn) -> FactValue:
        handlers: Mapping[str, Callable[[Fact, AgentTurn], FactValue]] = {
            "sales_total": lambda f, _: self._summary(f, "none").total_untaxed,
            "order_count": lambda f, _: float(self._summary(f, "none").order_count),
            "customer_sales": lambda f, _: self._customer_sales(f),
            "top_customers": lambda _, t: self._top_customers(t),
            "top_product": lambda f, _: [self._summary(f, "product").groups[0].key_name],
            "low_stock": lambda _f, _t: [
                item.product_name for item in self.gateway.low_stock_products(GROUP_LIMIT)
            ],
            "overdue_invoices": lambda f, _: [inv.name for inv in self._overdue(f)],
            "overdue_total": lambda f, _: sum(inv.amount_residual for inv in self._overdue(f)),
            "order": lambda f, _: self._order_field(f),
            "product": lambda f, _: self._product_field(f),
            "partner": lambda f, _: self._partner_field(f),
        }
        return handlers[fact.fact](fact, turn)

    def _summary(self, fact: Fact, group_by: GroupBy) -> SalesSummary:
        if fact.period is None:
            raise FactError(f"fact {fact.fact} needs a period")
        date_from, date_to = period_bounds(fact.period, self.today)
        summary = self.gateway.sales_summary(
            date_from, date_to, group_by=group_by, limit=GROUP_LIMIT
        )
        if group_by == "product" and not summary.groups:
            raise FactError(f"no sales in {fact.period}")
        return summary

    def _customer_sales(self, fact: Fact) -> float:
        partner = self._partner(fact)
        summary = self._summary(fact, "partner")
        return sum(g.total_untaxed for g in summary.groups if g.key_id == partner.id)

    def _top_customers(self, turn: AgentTurn) -> list[str]:
        periods = [
            arguments
            for call in turn.tool_calls
            if call.name == "sales_summary"
            and (arguments := json.loads(call.arguments)).get("group_by") == "partner"
        ]
        if not periods:
            raise FactError("the agent did not call sales_summary by partner")
        # Judge the ranking over the period the model chose.
        chosen = periods[-1]
        summary = self.gateway.sales_summary(
            date.fromisoformat(str(chosen["date_from"])),
            date.fromisoformat(str(chosen["date_to"])),
            group_by="partner",
            limit=TOP_CUSTOMERS,
        )
        return [group.key_name for group in summary.groups]

    def _overdue(self, fact: Fact) -> list[OverdueInvoice]:
        partner_id = self._partner(fact).id if fact.partner else None
        return list(self.gateway.overdue_invoices(partner_id, self.today))

    def _order_field(self, fact: Fact) -> FactValue:
        order = self._order(fact)
        if fact.field == "partner_name":
            return [order.partner_name]
        if fact.field == "amount_total":
            return order.amount_total
        return [line.product_name for line in order.lines]

    def _product_field(self, fact: Fact) -> FactValue:
        product = self._product(fact)
        if fact.field == "list_price":
            return product.list_price
        if product.default_code is None:
            raise FactError(f"product {product.name} has no reference")
        return [product.default_code]

    def _partner_field(self, fact: Fact) -> FactValue:
        partner = self._partner(fact)
        value = partner.city if fact.field == "city" else partner.email
        if value is None:
            raise FactError(f"partner {partner.name} has no {fact.field}")
        return [value]

    def _partner(self, fact: Fact) -> Partner:
        name = fact.partner or ""
        for partner in self.gateway.search_partners(name, LOOKUP_LIMIT):
            if partner.name == name:
                return partner
        raise FactError(f"partner {name} not found")

    def _product(self, fact: Fact) -> Product:
        name = fact.product or ""
        for product in self.gateway.search_products(name, LOOKUP_LIMIT):
            if plain_name(product.name) == name:
                return product
        raise FactError(f"product {name} not found")

    def _order(self, fact: Fact) -> SaleOrderDetail:
        order = self.gateway.get_sale_order(fact.order or "")
        if order is None:
            raise FactError(f"order {fact.order} not found")
        return order
