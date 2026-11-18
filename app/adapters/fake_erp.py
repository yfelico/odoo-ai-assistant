"""In-memory ERPGateway for tests."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.domain.models import GroupBy, Partner, SalesGroup, SalesSummary

CONFIRMED = "sale"


@dataclass(frozen=True, slots=True)
class FakeOrderLine:
    product_id: int
    product_name: str
    subtotal: float


@dataclass(frozen=True, slots=True)
class FakeSaleOrder:
    id: int
    partner_id: int
    partner_name: str
    date_order: datetime
    state: str
    lines: tuple[FakeOrderLine, ...]


@dataclass
class _Bucket:
    name: str = ""
    total: float = 0.0
    order_ids: set[int] = field(default_factory=set)

    def add(self, amount: float, order_id: int) -> None:
        self.total += amount
        self.order_ids.add(order_id)


class FakeERP:
    def __init__(
        self,
        partners: Iterable[Partner],
        orders: Iterable[FakeSaleOrder],
        *,
        reachable: bool = True,
    ) -> None:
        self._partners = list(partners)
        self._orders = list(orders)
        self._reachable = reachable

    def search_partners(self, name: str, limit: int) -> list[Partner]:
        needle = name.lower()
        return [p for p in self._partners if needle in p.name.lower()][:limit]

    def sales_summary(
        self, date_from: date, date_to: date, group_by: GroupBy, limit: int
    ) -> SalesSummary:
        start = datetime.combine(date_from, time.min)
        end = datetime.combine(date_to + timedelta(days=1), time.min)
        orders = [o for o in self._orders if o.state == CONFIRMED and start <= o.date_order < end]
        return SalesSummary(
            date_from=date_from,
            date_to=date_to,
            group_by=group_by,
            total_untaxed=sum(line.subtotal for o in orders for line in o.lines),
            order_count=len(orders),
            groups=_group(orders, group_by)[:limit],
        )

    def ping(self) -> bool:
        return self._reachable


def _group(orders: list[FakeSaleOrder], group_by: GroupBy) -> tuple[SalesGroup, ...]:
    if group_by == "none":
        return ()
    buckets: dict[int, _Bucket] = defaultdict(_Bucket)
    for order in orders:
        for line in order.lines:
            key, name = (
                (order.partner_id, order.partner_name)
                if group_by == "partner"
                else (line.product_id, line.product_name)
            )
            buckets[key].name = name
            buckets[key].add(line.subtotal, order.id)
    groups = [
        SalesGroup(
            key_id=key,
            key_name=bucket.name,
            total_untaxed=bucket.total,
            order_count=len(bucket.order_ids),
        )
        for key, bucket in buckets.items()
    ]
    return tuple(sorted(groups, key=lambda g: (-g.total_untaxed, g.key_id)))
