import json
from unittest.mock import MagicMock, patch

import requests

from odoo.tests import HttpCase, new_test_user, tagged

API_URL = "http://assistant.test:8000"
# Test-only value, not a real credential.
API_TOKEN = "demo-token"  # noqa: S105
PASSWORD = "demo-password-123"  # noqa: S105
CHAT = "/ai_assistant_chat/chat"
CONFIRM = "/ai_assistant_chat/confirm"
POST = "requests.post"
USER_ERROR = "odoo.exceptions.UserError"


def _api_reply(status: int, body: dict) -> MagicMock:
    response = MagicMock(status_code=status, ok=200 <= status < 300)
    response.json.return_value = body
    return response


@tagged("post_install", "-at_install")
class TestAssistantProxy(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("ai_assistant_chat.api_url", API_URL + "/")
        params.set_param("ai_assistant_chat.api_token", API_TOKEN)
        cls.seller = new_test_user(
            cls.env,
            login="ai_chat_seller",
            password=PASSWORD,
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        cls.employee = new_test_user(
            cls.env, login="ai_chat_employee", password=PASSWORD, groups="base.group_user"
        )

    def _rpc(self, route: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "method": "call", "id": 1, "params": params}
        response = self.url_open(
            route, data=json.dumps(payload), headers={"Content-Type": "application/json"}
        )
        return response.json()

    def test_chat_is_forwarded_to_the_api_with_its_token(self):
        self.authenticate("ai_chat_seller", PASSWORD)
        body = {"conversation_id": "c1", "answer": "Hola", "pending_draft": None, "tool_calls": []}

        with patch(POST, return_value=_api_reply(200, body)) as post:
            reply = self._rpc(CHAT, {"message": "hola", "conversation_id": "c1"})

        self.assertEqual(reply["result"], body)
        post.assert_called_once()
        self.assertEqual(post.call_args.args, (API_URL + "/chat",))
        self.assertEqual(
            post.call_args.kwargs["json"], {"message": "hola", "conversation_id": "c1"}
        )
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": f"Bearer {API_TOKEN}"})
        self.assertGreater(post.call_args.kwargs["timeout"], 60)

    def test_new_conversations_send_no_conversation_id(self):
        self.authenticate("ai_chat_seller", PASSWORD)
        body = {"conversation_id": "c2", "answer": "Hola", "pending_draft": None, "tool_calls": []}

        with patch(POST, return_value=_api_reply(200, body)) as post:
            self._rpc(CHAT, {"message": "hola"})

        self.assertEqual(post.call_args.kwargs["json"], {"message": "hola"})

    def test_without_a_token_no_authorization_header_is_sent(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant_chat.api_token", False)
        self.authenticate("ai_chat_seller", PASSWORD)
        body = {"conversation_id": "c3", "answer": "Hola", "pending_draft": None, "tool_calls": []}

        with patch(POST, return_value=_api_reply(200, body)) as post:
            self._rpc(CHAT, {"message": "hola"})

        self.assertEqual(post.call_args.kwargs["headers"], {})

    def test_confirm_sends_the_odoo_user_who_confirmed(self):
        self.authenticate("ai_chat_seller", PASSWORD)
        body = {"quotation_id": 40, "name": "S00040", "amount_untaxed": 700, "amount_total": 805}

        with patch(POST, return_value=_api_reply(200, body)) as post:
            reply = self._rpc(CONFIRM, {"draft_id": "d1"})

        self.assertEqual(reply["result"], body)
        self.assertEqual(post.call_args.args, (API_URL + "/confirm",))
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"draft_id": "d1", "confirmed_by_user_id": self.seller.id},
        )

    def test_users_outside_sales_cannot_use_the_assistant(self):
        self.authenticate("ai_chat_employee", PASSWORD)

        with patch(POST) as post:
            reply = self._rpc(CHAT, {"message": "hola"})

        self.assertEqual(reply["error"]["data"]["name"], "odoo.exceptions.AccessError")
        post.assert_not_called()

    def test_anonymous_visitors_cannot_use_the_assistant(self):
        with patch(POST) as post:
            reply = self._rpc(CHAT, {"message": "hola"})

        self.assertIn("error", reply)
        post.assert_not_called()

    def test_api_errors_become_readable_messages(self):
        self.authenticate("ai_chat_seller", PASSWORD)
        cases = [
            (401, "API token"),
            (404, "no longer available"),
            (422, "could not be processed"),
            (502, "Odoo rejected"),
            (503, "not available right now"),
            (500, "unexpected error"),
        ]
        for status, text in cases:
            with (
                self.subTest(status=status),
                patch(POST, return_value=_api_reply(status, {"detail": "x"})),
            ):
                reply = self._rpc(CONFIRM, {"draft_id": "d1"})

                self.assertEqual(reply["error"]["data"]["name"], USER_ERROR)
                self.assertIn(text, reply["error"]["data"]["message"])

    def test_an_unreachable_api_becomes_a_readable_message(self):
        self.authenticate("ai_chat_seller", PASSWORD)

        with patch(POST, side_effect=requests.ConnectionError("refused")):
            reply = self._rpc(CHAT, {"message": "hola"})

        self.assertEqual(reply["error"]["data"]["name"], USER_ERROR)
        self.assertIn("cannot reach the assistant", reply["error"]["data"]["message"])
