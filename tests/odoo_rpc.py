"""Raw XML-RPC access for tests that must inspect or clean up Odoo beyond what the app can do."""

import xmlrpc.client

from app.config import Settings


def odoo_execute(
    settings: Settings,
    model: str,
    method: str,
    args: list[object],
    kwargs: dict[str, object] | None = None,
) -> object:
    url = settings.odoo_url.rstrip("/")
    key = settings.odoo_api_key.get_secret_value()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(settings.odoo_db, settings.odoo_username, key, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
    return models.execute_kw(settings.odoo_db, uid, key, model, method, args, kwargs or {})


def sale_order_count(settings: Settings) -> int:
    count = odoo_execute(settings, "sale.order", "search_count", [[]])
    assert isinstance(count, int)
    return count
