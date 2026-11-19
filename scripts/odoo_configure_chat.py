"""Store the API token of the AI assistant in Odoo, for the ai_assistant_chat module.

Runs inside ``odoo shell`` (which provides ``env``), fed through stdin by
scripts/init_odoo.sh with ``API_TOKEN`` in the environment. An empty token clears
the parameter, for an API without token.
"""

import os

TOKEN_PARAM = "ai_assistant_chat.api_token"  # noqa: S105

env = globals()["env"]  # injected by odoo shell
env["ir.config_parameter"].sudo().set_param(TOKEN_PARAM, os.environ.get("API_TOKEN") or False)
env.cr.commit()
print("ai_assistant_chat: API token configured")
