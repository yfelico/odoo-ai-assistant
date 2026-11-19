from odoo.tests import TransactionCase, tagged

API_URL_PARAM = "ai_assistant_chat.api_url"
API_TOKEN_PARAM = "ai_assistant_chat.api_token"  # noqa: S105


@tagged("post_install", "-at_install")
class TestSettings(TransactionCase):
    def test_default_api_url_reaches_the_api_inside_docker(self):
        url = self.env["ir.config_parameter"].sudo().get_param(API_URL_PARAM)

        self.assertEqual(url, "http://api:8000")

    def test_settings_store_url_and_token_as_system_parameters(self):
        settings = self.env["res.config.settings"].create(
            {
                "ai_assistant_api_url": "http://assistant.example:8000",
                "ai_assistant_api_token": "demo-token",
            }
        )

        settings.execute()

        params = self.env["ir.config_parameter"].sudo()
        self.assertEqual(params.get_param(API_URL_PARAM), "http://assistant.example:8000")
        self.assertEqual(params.get_param(API_TOKEN_PARAM), "demo-token")
