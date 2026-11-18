"""ERPGateway implementation over Odoo's external XML-RPC API."""

import http.client
import logging
import xmlrpc.client
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol, Self, cast

from app.config import Settings
from app.domain.models import (
    CreatedQuotation,
    ERPAuthenticationError,
    ERPError,
    ERPUnavailableError,
    GroupBy,
    LowStockItem,
    OverdueInvoice,
    Partner,
    Product,
    QuotationLine,
    SaleOrderDetail,
    SaleOrderLine,
    SalesGroup,
    SalesSummary,
)

logger = logging.getLogger(__name__)

COMMON_PATH = "/xmlrpc/2/common"
OBJECT_PATH = "/xmlrpc/2/object"
ODOO_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ODOO_DATE_FORMAT = "%Y-%m-%d"

PARTNER_MODEL = "res.partner"
PARTNER_FIELDS = ["id", "name", "email", "city"]
PRODUCT_MODEL = "product.product"
PRODUCT_FIELDS = ["id", "display_name", "default_code", "lst_price", "uom_id"]
ORDERPOINT_MODEL = "stock.warehouse.orderpoint"
ORDERPOINT_FIELDS = ["product_id", "qty_on_hand", "qty_forecast", "product_min_qty"]
INVOICE_MODEL = "account.move"
INVOICE_FIELDS = [
    "id",
    "name",
    "commercial_partner_id",
    "invoice_date_due",
    "amount_residual",
    "amount_total",
]
SALE_ORDER_MODEL = "sale.order"
SALE_ORDER_FIELDS = [
    "id",
    "name",
    "partner_id",
    "date_order",
    "state",
    "amount_untaxed",
    "amount_total",
]
SALE_ORDER_LINE_MODEL = "sale.order.line"
SALE_ORDER_LINE_FIELDS = ["product_id", "name", "product_uom_qty", "price_unit", "price_subtotal"]
SEARCH_READ = "search_read"
READ_GROUP = "read_group"
PRODUCT_FIELD = "product_id"
CREATED_QUOTATION_FIELDS = ["id", "name", "amount_untaxed", "amount_total"]
NOTE_SUBTYPE = "mail.mt_note"  # internal note: followers are not emailed
MANY2ONE_LENGTH = 2  # Odoo many2one values are [id, display_name]
ORDER_TOTAL_FIELD = "amount_untaxed"
LINE_TOTAL_FIELD = "price_subtotal"
COUNT_KEY = "__count"

type DomainLeaf = list[str | bool]
type Domain = list[DomainLeaf]
type Record = dict[str, object]


class OdooEndpoint(Protocol):
    """Methods used on Odoo's XML-RPC endpoints (common and object)."""

    def version(self) -> dict[str, object]: ...

    def authenticate(
        self, db: str, login: str, password: str, env: dict[str, object]
    ) -> int | bool: ...

    # Mirrors Odoo's execute_kw signature, hence the many positional parameters.
    def execute_kw(  # noqa: PLR0913, PLR0917
        self,
        db: str,
        uid: int,
        password: str,
        model: str,
        method: str,
        args: list[object],
        kwargs: dict[str, object] | None = None,
    ) -> object: ...


type ProxyFactory = Callable[[str], OdooEndpoint]


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: tuple[str, dict[str, str]] | str) -> http.client.HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class _SafeTimeoutTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(
        self, host: tuple[str, dict[str, str]] | str
    ) -> http.client.HTTPSConnection:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


def xmlrpc_proxy_factory(timeout: float) -> ProxyFactory:
    def factory(url: str) -> OdooEndpoint:
        transport: xmlrpc.client.Transport = (
            _SafeTimeoutTransport(timeout)
            if url.startswith("https")
            else _TimeoutTransport(timeout)
        )
        proxy = xmlrpc.client.ServerProxy(url, transport=transport, allow_none=True)
        # ServerProxy resolves remote methods dynamically.
        return cast(OdooEndpoint, proxy)

    return factory


@dataclass(frozen=True, slots=True)
class _GroupQuery:
    model: str
    domain: Domain
    total_field: str
    count_spec: str | None  # extra aggregate; None means use __count
    groupby: str


class OdooXmlRpcGateway:
    def __init__(
        self,
        *,
        url: str,
        db: str,
        username: str,
        api_key: str,
        proxy_factory: ProxyFactory | None = None,
    ) -> None:
        factory = proxy_factory or xmlrpc_proxy_factory(timeout=10.0)
        base_url = url.rstrip("/")
        self._common = factory(f"{base_url}{COMMON_PATH}")
        self._object = factory(f"{base_url}{OBJECT_PATH}")
        self._db = db
        self._username = username
        self._api_key = api_key
        self._uid: int | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            url=settings.odoo_url,
            db=settings.odoo_db,
            username=settings.odoo_username,
            api_key=settings.odoo_api_key.get_secret_value(),
            proxy_factory=xmlrpc_proxy_factory(settings.odoo_timeout_seconds),
        )

    def search_partners(self, name: str, limit: int) -> list[Partner]:
        rows = self._execute(
            PARTNER_MODEL,
            SEARCH_READ,
            [[["name", "ilike", name]]],
            {"fields": PARTNER_FIELDS, "limit": limit},
        )
        return [_partner(row) for row in _records(rows)]

    def get_partner(self, partner_id: int) -> Partner | None:
        rows = _records(
            self._execute(
                PARTNER_MODEL,
                SEARCH_READ,
                [[["id", "=", partner_id]]],
                {"fields": PARTNER_FIELDS, "limit": 1},
            )
        )
        return _partner(rows[0]) if rows else None

    def get_products(self, product_ids: Iterable[int]) -> list[Product]:
        rows = self._execute(
            PRODUCT_MODEL,
            SEARCH_READ,
            [[["id", "in", list(product_ids)], ["sale_ok", "=", True]]],
            {"fields": PRODUCT_FIELDS},
        )
        return [_product(row) for row in _records(rows)]

    def create_quotation(
        self, partner_id: int, lines: Sequence[QuotationLine], note: str
    ) -> CreatedQuotation:
        # Odoo prices the lines itself (pricelist, taxes); only product and quantity are sent.
        order_lines = [
            [0, 0, {"product_id": line.product_id, "product_uom_qty": line.quantity}]
            for line in lines
        ]
        created = self._execute(
            SALE_ORDER_MODEL, "create", [{"partner_id": partner_id, "order_line": order_lines}], {}
        )
        if isinstance(created, bool) or not isinstance(created, int):
            raise ERPError("Odoo did not return the new quotation id")
        logger.info("Created quotation %s for partner %s", created, partner_id)
        self._post_note(created, note)
        rows = _records(
            self._execute(
                SALE_ORDER_MODEL,
                SEARCH_READ,
                [[["id", "=", created]]],
                {"fields": CREATED_QUOTATION_FIELDS, "limit": 1},
            )
        )
        if not rows:
            raise ERPError("The new quotation could not be read back")
        return CreatedQuotation(
            id=_int(rows[0], "id"),
            name=_str(rows[0], "name"),
            amount_untaxed=_amount(rows[0], "amount_untaxed"),
            amount_total=_amount(rows[0], "amount_total"),
        )

    def search_products(self, name: str, limit: int) -> list[Product]:
        domain: list[object] = [
            "&",
            ["sale_ok", "=", True],
            "|",
            ["name", "ilike", name],
            ["default_code", "ilike", name],
        ]
        rows = self._execute(
            PRODUCT_MODEL, SEARCH_READ, [domain], {"fields": PRODUCT_FIELDS, "limit": limit}
        )
        return [_product(row) for row in _records(rows)]

    def low_stock_products(self, limit: int) -> list[LowStockItem]:
        # qty_on_hand is computed (not stored), so the filter runs here, not in Odoo.
        rows = self._execute(ORDERPOINT_MODEL, SEARCH_READ, [[]], {"fields": ORDERPOINT_FIELDS})
        items = [_low_stock_item(row) for row in _records(rows)]
        low = [item for item in items if item.qty_on_hand < item.min_qty]
        low.sort(key=lambda item: (item.qty_on_hand - item.min_qty, item.product_id))
        return low[:limit]

    def overdue_invoices(self, partner_id: int | None, as_of: date) -> list[OverdueInvoice]:
        domain: list[object] = [
            ["move_type", "=", "out_invoice"],
            ["state", "=", "posted"],
            ["payment_state", "in", ["not_paid", "partial"]],
            ["invoice_date_due", "<", as_of.strftime(ODOO_DATE_FORMAT)],
        ]
        if partner_id is not None:
            # child_of also matches invoices addressed to the customer's contacts.
            domain.append(["partner_id", "child_of", partner_id])
        rows = self._execute(
            INVOICE_MODEL,
            SEARCH_READ,
            [domain],
            {"fields": INVOICE_FIELDS, "order": "invoice_date_due asc, id asc"},
        )
        return [_overdue_invoice(row) for row in _records(rows)]

    def get_sale_order(self, name: str) -> SaleOrderDetail | None:
        orders = _records(
            self._execute(
                SALE_ORDER_MODEL,
                SEARCH_READ,
                [[["name", "=ilike", _escape_like(name)]]],
                {"fields": SALE_ORDER_FIELDS, "limit": 1},
            )
        )
        if not orders:
            return None
        order = orders[0]
        order_id = _int(order, "id")
        lines = _records(
            self._execute(
                SALE_ORDER_LINE_MODEL,
                SEARCH_READ,
                # display_type marks section and note lines, which carry no product.
                [[["order_id", "=", order_id], ["display_type", "=", False]]],
                {"fields": SALE_ORDER_LINE_FIELDS, "order": "sequence, id"},
            )
        )
        partner_id, partner_name = _many2one(order, "partner_id")
        return SaleOrderDetail(
            id=order_id,
            name=_str(order, "name"),
            partner_id=partner_id,
            partner_name=partner_name,
            date_order=_datetime(order, "date_order"),
            state=_str(order, "state"),
            amount_untaxed=_amount(order, "amount_untaxed"),
            amount_total=_amount(order, "amount_total"),
            lines=tuple(_sale_order_line(row) for row in lines),
        )

    def sales_summary(
        self, date_from: date, date_to: date, group_by: GroupBy, limit: int
    ) -> SalesSummary:
        order_domain = _confirmed_orders_domain(date_from, date_to)
        overall = _records(
            self._execute(
                SALE_ORDER_MODEL,
                READ_GROUP,
                [order_domain, [f"{ORDER_TOTAL_FIELD}:sum"], []],
                {"lazy": False},
            )
        )
        total, count = (
            (_amount(overall[0], ORDER_TOTAL_FIELD), _int(overall[0], COUNT_KEY))
            if overall
            else (0.0, 0)
        )
        groups = () if group_by == "none" else self._groups(group_by, order_domain, limit)
        return SalesSummary(
            date_from=date_from,
            date_to=date_to,
            group_by=group_by,
            total_untaxed=total,
            order_count=count,
            groups=groups,
        )

    def _post_note(self, order_id: int, note: str) -> None:
        try:
            self._execute(
                SALE_ORDER_MODEL,
                "message_post",
                [[order_id]],
                {"body": note, "message_type": "comment", "subtype_xmlid": NOTE_SUBTYPE},
            )
        except ERPError:
            # The quotation already exists: failing here would invite a duplicate on retry.
            logger.warning("Quotation %s created but its chatter note failed", order_id)

    def ping(self) -> bool:
        try:
            self._common.version()
        except (OSError, xmlrpc.client.Error):
            logger.warning("Odoo did not answer the version check")
            return False
        return True

    def _groups(
        self, group_by: GroupBy, order_domain: Domain, limit: int
    ) -> tuple[SalesGroup, ...]:
        query = _group_query(group_by, order_domain)
        fields = [f"{query.total_field}:sum"]
        if query.count_spec is not None:
            fields.append(query.count_spec)
        rows = _records(
            self._execute(
                query.model,
                READ_GROUP,
                [query.domain, fields, [query.groupby]],
                {"lazy": False, "orderby": f"{query.total_field} desc", "limit": limit},
            )
        )
        count_key = COUNT_KEY if query.count_spec is None else query.count_spec.split(":")[0]
        groups: list[SalesGroup] = []
        for row in rows:
            key_id, key_name = _many2one(row, query.groupby)
            groups.append(
                SalesGroup(
                    key_id=key_id,
                    key_name=key_name,
                    total_untaxed=_amount(row, query.total_field),
                    order_count=_int(row, count_key),
                )
            )
        return tuple(groups)

    def _execute(
        self, model: str, method: str, args: list[object], kwargs: dict[str, object]
    ) -> object:
        uid = self._authenticate()
        return _guarded(
            lambda: self._object.execute_kw(
                self._db, uid, self._api_key, model, method, args, kwargs
            )
        )

    def _authenticate(self) -> int:
        if self._uid is None:
            uid = _guarded(
                lambda: self._common.authenticate(self._db, self._username, self._api_key, {})
            )
            if isinstance(uid, bool) or not isinstance(uid, int):
                raise ERPAuthenticationError("Odoo rejected the configured credentials")
            self._uid = uid
            logger.info("Authenticated against Odoo as uid %s", uid)
        return self._uid


def _guarded[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except xmlrpc.client.Fault as err:
        # faultString may embed record data, so only the code is surfaced.
        raise ERPError(f"Odoo rejected the request (fault {err.faultCode})") from err
    except (OSError, xmlrpc.client.ProtocolError) as err:
        raise ERPUnavailableError("Odoo is unreachable") from err


def _confirmed_orders_domain(date_from: date, date_to: date) -> Domain:
    # date_order is stored in UTC; the end date is inclusive.
    start = datetime.combine(date_from, time.min).strftime(ODOO_DATETIME_FORMAT)
    end = datetime.combine(date_to + timedelta(days=1), time.min).strftime(ODOO_DATETIME_FORMAT)
    return [
        ["state", "=", "sale"],
        ["date_order", ">=", start],
        ["date_order", "<", end],
    ]


def _group_query(group_by: GroupBy, order_domain: Domain) -> _GroupQuery:
    if group_by == "partner":
        return _GroupQuery(
            model=SALE_ORDER_MODEL,
            domain=order_domain,
            total_field=ORDER_TOTAL_FIELD,
            count_spec=None,
            groupby="partner_id",
        )
    # sale.order has no product field, so products are grouped on order lines.
    line_domain: Domain = [[f"order_id.{leaf[0]}", *leaf[1:]] for leaf in order_domain]
    line_domain.append([PRODUCT_FIELD, "!=", False])
    return _GroupQuery(
        model=SALE_ORDER_LINE_MODEL,
        domain=line_domain,
        total_field=LINE_TOTAL_FIELD,
        count_spec="order_id:count_distinct",
        groupby=PRODUCT_FIELD,
    )


def _partner(row: Record) -> Partner:
    return Partner(
        id=_int(row, "id"),
        name=_str(row, "name"),
        email=_optional_str(row, "email"),
        city=_optional_str(row, "city"),
    )


def _product(row: Record) -> Product:
    return Product(
        id=_int(row, "id"),
        name=_str(row, "display_name"),
        default_code=_optional_str(row, "default_code"),
        list_price=_amount(row, "lst_price"),
        uom=_many2one(row, "uom_id")[1],
    )


def _escape_like(value: str) -> str:
    # =ilike treats % and _ as wildcards; Odoo uses backslash as the escape character.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _low_stock_item(row: Record) -> LowStockItem:
    product_id, product_name = _many2one(row, PRODUCT_FIELD)
    return LowStockItem(
        product_id=product_id,
        product_name=product_name,
        qty_on_hand=_amount(row, "qty_on_hand"),
        qty_forecast=_amount(row, "qty_forecast"),
        min_qty=_amount(row, "product_min_qty"),
    )


def _overdue_invoice(row: Record) -> OverdueInvoice:
    partner_id, partner_name = _many2one(row, "commercial_partner_id")
    return OverdueInvoice(
        id=_int(row, "id"),
        name=_str(row, "name"),
        partner_id=partner_id,
        partner_name=partner_name,
        due_date=_date(row, "invoice_date_due"),
        amount_residual=_amount(row, "amount_residual"),
        amount_total=_amount(row, "amount_total"),
    )


def _sale_order_line(row: Record) -> SaleOrderLine:
    product_id, product_name = _many2one(row, PRODUCT_FIELD)
    return SaleOrderLine(
        product_id=product_id,
        product_name=product_name,
        description=_str(row, "name"),
        quantity=_amount(row, "product_uom_qty"),
        price_unit=_amount(row, "price_unit"),
        subtotal=_amount(row, "price_subtotal"),
    )


def _date(row: Record, key: str) -> date:
    try:
        return datetime.strptime(_str(row, key), ODOO_DATE_FORMAT).date()
    except ValueError as err:
        raise ERPError(f"Expected a date in field {key!r}") from err


def _datetime(row: Record, key: str) -> datetime:
    try:
        return datetime.strptime(_str(row, key), ODOO_DATETIME_FORMAT)
    except ValueError as err:
        raise ERPError(f"Expected a datetime in field {key!r}") from err


def _records(value: object) -> list[Record]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ERPError("Unexpected response shape from Odoo")
    return cast(list[Record], value)


def _int(row: Record, key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ERPError(f"Expected an integer in field {key!r}")
    return value


def _str(row: Record, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ERPError(f"Expected text in field {key!r}")
    return value


def _optional_str(row: Record, key: str) -> str | None:
    # Odoo returns False for empty fields.
    value = row.get(key)
    return value if isinstance(value, str) else None


def _amount(row: Record, key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool):  # False: empty aggregate
        return 0.0
    if not isinstance(value, int | float):
        raise ERPError(f"Expected a number in field {key!r}")
    return float(value)


def _many2one(row: Record, key: str) -> tuple[int, str]:
    value = row.get(key)
    if not (
        isinstance(value, list)
        and len(value) == MANY2ONE_LENGTH
        and isinstance(value[0], int)
        and isinstance(value[1], str)
    ):
        raise ERPError(f"Expected [id, name] in field {key!r}")
    return value[0], value[1]
