"""Create or refresh the AI assistant's technical user and its API key.

Runs inside ``odoo shell`` (which provides ``env``), fed through stdin by
scripts/init_odoo.sh. Prints exactly one ``ODOO_API_KEY=<key>`` line on stdout.
Re-running rotates the key.
"""

import os
import secrets

LOGIN = os.environ.get("ODOO_TECH_LOGIN", "ai_assistant")
KEY_NAME = "ai-assistant"
# Odoo needs an author email to post chatter notes; example.com is reserved for examples.
EMAIL = "ai-assistant@example.com"
# Least privilege available with standard groups: read all sales orders,
# stock and invoices. These groups still allow some writes (see README).
GROUP_XMLIDS = (
    "base.group_user",
    "sales_team.group_sale_salesman_all_leads",
    "stock.group_stock_user",
    "account.group_account_readonly",
)

env = globals()["env"]  # injected by odoo shell
group_ids = [env.ref(xmlid).id for xmlid in GROUP_XMLIDS]
users = env["res.users"].with_context(no_reset_password=True)
user = users.search([("login", "=", LOGIN)], limit=1)
if user:
    user.write({"groups_id": [(6, 0, group_ids)], "email": EMAIL})
else:
    user = users.create(
        {
            "name": "AI Assistant",
            "login": LOGIN,
            "email": EMAIL,
            # Never used: the API authenticates with the API key only.
            "password": secrets.token_urlsafe(32),
            "groups_id": [(6, 0, group_ids)],
        }
    )

api_keys = env["res.users.apikeys"]
api_keys.sudo().search([("user_id", "=", user.id), ("name", "=", KEY_NAME)]).unlink()
# Odoo 18 requires an expiration date for non-admin users unless the key is
# generated in sudo; the key still belongs to the technical user. It does not
# expire: rotate it by re-running init_odoo.sh.
key = api_keys.with_user(user).sudo()._generate("rpc", KEY_NAME, None)
env.cr.commit()

# The key line is consumed by init_odoo.sh.
print(f"ODOO_API_KEY={key}")  # noqa: T201
