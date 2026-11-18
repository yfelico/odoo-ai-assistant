"""Behavioural contract every ERPGateway implementation must satisfy.

The checks are data-independent so the same suite runs against FakeERP (unit)
and against the real Odoo demo database (integration). Subclasses provide a
``gateway`` fixture and the sample-data attributes below.
"""

from datetime import date

import pytest

from app.domain.models import GroupBy
from app.domain.ports import ERPGateway

EMPTY_PERIOD = (date(1990, 1, 1), date(1990, 1, 31))
NO_MATCH_QUERY = "zzz-no-such-partner-zzz"
LARGE_LIMIT = 1000


class ERPGatewayContract:
    # Name fragment (lowercase) matching at least two partners.
    partner_query: str
    # Period containing confirmed sales for at least two partners and two products.
    sales_period: tuple[date, date]
    # Name or reference fragment (lowercase) matching at least two saleable products.
    product_query: str
    # Existing sale order name, as stored (e.g. "S00023").
    order_name: str
    # Date before which at least one customer invoice is due and unpaid.
    overdue_as_of: date

    def test_search_partners_matches_fragment_ignoring_case(self, gateway: ERPGateway) -> None:
        partners = gateway.search_partners(self.partner_query.upper(), limit=20)

        assert partners
        assert all(self.partner_query in p.name.lower() for p in partners)

    def test_search_partners_respects_limit(self, gateway: ERPGateway) -> None:
        assert len(gateway.search_partners(self.partner_query, limit=1)) == 1

    def test_search_partners_without_match_returns_empty_list(self, gateway: ERPGateway) -> None:
        assert gateway.search_partners(NO_MATCH_QUERY, limit=5) == []

    def test_sales_summary_of_empty_period_is_zero(self, gateway: ERPGateway) -> None:
        summary = gateway.sales_summary(*EMPTY_PERIOD, group_by="none", limit=10)

        assert summary.total_untaxed == 0
        assert summary.order_count == 0
        assert summary.groups == ()

    def test_sales_summary_without_grouping_has_no_groups(self, gateway: ERPGateway) -> None:
        summary = gateway.sales_summary(*self.sales_period, group_by="none", limit=10)

        assert summary.total_untaxed > 0
        assert summary.order_count > 0
        assert summary.groups == ()

    @pytest.mark.parametrize("group_by", ["partner", "product"])
    def test_sales_summary_groups_add_up_to_total(
        self, gateway: ERPGateway, group_by: GroupBy
    ) -> None:
        summary = gateway.sales_summary(*self.sales_period, group_by=group_by, limit=LARGE_LIMIT)

        assert len(summary.groups) >= 2
        assert sum(g.total_untaxed for g in summary.groups) == pytest.approx(summary.total_untaxed)

    @pytest.mark.parametrize("group_by", ["partner", "product"])
    def test_sales_summary_groups_are_sorted_desc_and_limited(
        self, gateway: ERPGateway, group_by: GroupBy
    ) -> None:
        summary = gateway.sales_summary(*self.sales_period, group_by=group_by, limit=1)
        full = gateway.sales_summary(*self.sales_period, group_by=group_by, limit=LARGE_LIMIT)

        totals = [g.total_untaxed for g in full.groups]
        assert totals == sorted(totals, reverse=True)
        assert summary.groups == full.groups[:1]
        assert summary.total_untaxed == pytest.approx(full.total_untaxed)

    def test_sales_summary_partner_order_counts_add_up(self, gateway: ERPGateway) -> None:
        summary = gateway.sales_summary(*self.sales_period, group_by="partner", limit=LARGE_LIMIT)

        assert sum(g.order_count for g in summary.groups) == summary.order_count

    def test_search_products_matches_name_or_reference_ignoring_case(
        self, gateway: ERPGateway
    ) -> None:
        products = gateway.search_products(self.product_query.upper(), limit=20)

        assert products
        assert all(
            self.product_query in p.name.lower()
            or self.product_query in (p.default_code or "").lower()
            for p in products
        )

    def test_search_products_respects_limit(self, gateway: ERPGateway) -> None:
        assert len(gateway.search_products(self.product_query, limit=1)) == 1

    def test_search_products_without_match_returns_empty_list(self, gateway: ERPGateway) -> None:
        assert gateway.search_products(NO_MATCH_QUERY, limit=5) == []

    def test_low_stock_products_are_below_their_minimum(self, gateway: ERPGateway) -> None:
        items = gateway.low_stock_products(limit=LARGE_LIMIT)

        assert items
        assert all(item.qty_on_hand < item.min_qty for item in items)

    def test_low_stock_products_sorted_by_shortfall_and_limited(self, gateway: ERPGateway) -> None:
        full = gateway.low_stock_products(limit=LARGE_LIMIT)

        shortfalls = [item.min_qty - item.qty_on_hand for item in full]
        assert shortfalls == sorted(shortfalls, reverse=True)
        assert gateway.low_stock_products(limit=1) == full[:1]

    def test_overdue_invoices_are_unpaid_and_due_before_as_of(self, gateway: ERPGateway) -> None:
        invoices = gateway.overdue_invoices(None, self.overdue_as_of)

        assert invoices
        assert all(inv.due_date < self.overdue_as_of for inv in invoices)
        assert all(inv.amount_residual > 0 for inv in invoices)

    def test_overdue_invoices_before_any_due_date_is_empty(self, gateway: ERPGateway) -> None:
        assert gateway.overdue_invoices(None, EMPTY_PERIOD[0]) == []

    def test_overdue_invoices_filters_by_partner(self, gateway: ERPGateway) -> None:
        first = gateway.overdue_invoices(None, self.overdue_as_of)[0]

        mine = gateway.overdue_invoices(first.partner_id, self.overdue_as_of)

        assert first in mine
        assert all(inv.partner_id == first.partner_id for inv in mine)

    def test_get_sale_order_returns_lines_adding_up_to_untaxed_total(
        self, gateway: ERPGateway
    ) -> None:
        order = gateway.get_sale_order(self.order_name)

        assert order is not None
        assert order.name == self.order_name
        assert order.lines
        assert sum(line.subtotal for line in order.lines) == pytest.approx(order.amount_untaxed)

    def test_get_sale_order_ignores_case(self, gateway: ERPGateway) -> None:
        order = gateway.get_sale_order(self.order_name.lower())

        assert order is not None
        assert order.name == self.order_name

    def test_get_sale_order_unknown_returns_none(self, gateway: ERPGateway) -> None:
        assert gateway.get_sale_order("ZZZ-NO-SUCH-ORDER") is None
