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
