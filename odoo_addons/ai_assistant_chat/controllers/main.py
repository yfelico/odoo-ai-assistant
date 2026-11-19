"""Server-side proxy between the chat panel and the AI assistant API.

The browser only talks to Odoo: the API URL and token stay on the server, and only
logged-in sales users can reach the assistant.
"""

import logging

import requests

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

API_URL_PARAM = "ai_assistant_chat.api_url"
API_TOKEN_PARAM = "ai_assistant_chat.api_token"  # noqa: S105
DEFAULT_API_URL = "http://api:8000"
SALES_GROUP = "sales_team.group_sale_salesman"
# Longer than the agent's own time budget (60 s), so the API answers first.
TIMEOUT_SECONDS = 90


class AssistantChatController(http.Controller):
    @http.route("/ai_assistant_chat/chat", type="json", auth="user", methods=["POST"])
    def chat(self, message, conversation_id=None):
        payload = {"message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return self._call_api("/chat", payload)

    @http.route("/ai_assistant_chat/confirm", type="json", auth="user", methods=["POST"])
    def confirm(self, draft_id):
        payload = {"draft_id": draft_id, "confirmed_by_user_id": request.env.uid}
        return self._call_api("/confirm", payload)

    def _call_api(self, path, payload):
        if not request.env.user.has_group(SALES_GROUP):
            raise AccessError(_("Only sales users can use the AI assistant."))
        params = request.env["ir.config_parameter"].sudo()
        url = (params.get_param(API_URL_PARAM) or DEFAULT_API_URL).rstrip("/") + path
        token = params.get_param(API_TOKEN_PARAM)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as err:
            _logger.warning("AI assistant API unreachable (user %s): %s", request.env.uid, err)
            raise UserError(
                _("Odoo cannot reach the assistant. Check the API URL in Settings.")
            ) from err
        if not response.ok:
            _logger.warning(
                "AI assistant API answered %s on %s (user %s)",
                response.status_code,
                path,
                request.env.uid,
            )
            raise UserError(_error_message(response.status_code))
        return response.json()


def _error_message(status):
    messages = {
        401: _("The assistant rejected Odoo's credentials. Check the API token in Settings."),
        404: _("This draft is no longer available. Ask the assistant to prepare it again."),
        422: _("The request could not be processed. Try rephrasing it."),
        502: _("Odoo rejected the quotation. The draft is kept: try again."),
        503: _("The assistant is not available right now. Try again in a few seconds."),
    }
    return messages.get(status, _("The assistant had an unexpected error. Try again later."))
