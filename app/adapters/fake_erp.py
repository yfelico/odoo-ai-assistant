"""In-memory ERPGateway for tests."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.domain.models import (
    GroupBy,
    LowStockItem,
    OverdueInvoice,
    Partner,
    Product,
    SaleOrderDetail,
    SaleOrderLine,
    SalesGroup,
    SalesSummary,
)

CONFIRMED = "sale"


@dataclass(frozen=True, slots=True)
class FakeOrderLine:
    product_id: int
    product_name: str
    subtotal: float
    quantity: float = 1.0


@dataclass(frozen=True, slots=True)
class FakeSaleOrder:
    id: int
    partner_id: int
    partner_name: str
    date_order: datetime
    state: str
    lines: tuple[FakeOrderLine, ...]
    name: str = ""


@dataclass(frozen=True, slots=True)
class FakeOrderpoint:
    product_id: int
    product_name: str
    qty_on_hand: float
    qty_forecast: float
    min_qty: float


@dataclass(frozen=True, slots=True)
class FakeInvoice:
    invoice: OverdueInvoice
    move_type: str = "out_invoice"
    state: str = "posted"
    payment_state: str = "not_paid"


@dataclass
class _Bucket:
    name: str = ""
    total: float = 0.0
    order_ids: set[int] = field(default_factory=set)

    def add(self, amount: float, order_id: int) -> None:
        self.total += amount
        self.order_ids.add(order_id)


class FakeERP:
    # One keyword per kind of seeded record.
    def __init__(  # noqa: PLR0913
        self,
        partners: Iterable[Partner],
        orders: Iterable[FakeSaleOrder],
        *,
        products: Iterable[Product] = (),
        orderpoints: Iterable[FakeOrderpoint] = (),
        invoices: Iterable[FakeInvoice] = (),
        reachable: bool = True,
    ) -> None:
        self._partners = list(partners)
        self._orders = list(orders)
        self._products = list(products)
        self._orderpoints = list(orderpoints)
        self._invoices = list(invoices)
        self._reachable = reachable

    def search_partners(self, name: str, limit: int) -> list[Partner]:
        needle = name.lower()
        return [p for p in self._partners if needle in p.name.lower()][:limit]

    def search_products(self, name: str, limit: int) -> list[Product]:
        needle = name.lower()
        return [
            p
            for p in self._products
            if needle in p.name.lower() or needle in (p.default_code or "").lower()
        ][:limit]

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

    def low_stock_products(self, limit: int) -> list[LowStockItem]:
        low = [
            LowStockItem(
                product_id=p.product_id,
                product_name=p.product_name,
                qty_on_hand=p.qty_on_hand,
                qty_forecast=p.qty_forecast,
                min_qty=p.min_qty,
            )
            for p in self._orderpoints
            if p.qty_on_hand < p.min_qty
        ]
        low.sort(key=lambda item: (item.qty_on_hand - item.min_qty, item.product_id))
        return low[:limit]

    def overdue_invoices(self, partner_id: int | None, as_of: date) -> list[OverdueInvoice]:
        overdue = [
            fake.invoice
            for fake in self._invoices
            if fake.move_type == "out_invoice"
            and fake.state == "posted"
            and fake.payment_state in {"not_paid", "partial"}
            and fake.invoice.due_date < as_of
            and partner_id in {None, fake.invoice.partner_id}
        ]
        return sorted(overdue, key=lambda inv: (inv.due_date, inv.id))

    def get_sale_order(self, name: str) -> SaleOrderDetail | None:
        order = next((o for o in self._orders if o.name.lower() == name.lower()), None)
        if order is None:
            return None
        untaxed = sum(line.subtotal for line in order.lines)
        return SaleOrderDetail(
            id=order.id,
            name=order.name,
            partner_id=order.partner_id,
            partner_name=order.partner_name,
            date_order=order.date_order,
            state=order.state,
            amount_untaxed=untaxed,
            amount_total=untaxed,
            lines=tuple(
                SaleOrderLine(
                    product_id=line.product_id,
                    product_name=line.product_name,
                    description=line.product_name,
                    quantity=line.quantity,
                    price_unit=line.subtotal / line.quantity,
                    subtotal=line.subtotal,
                )
                for line in order.lines
            ),
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
