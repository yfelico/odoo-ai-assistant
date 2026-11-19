from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ai_assistant_api_url = fields.Char(
        string="Assistant API URL",
        config_parameter="ai_assistant_chat.api_url",
        help="Base URL of the AI assistant API, as reached from the Odoo server.",
    )
    ai_assistant_api_token = fields.Char(
        string="Assistant API token",
        config_parameter="ai_assistant_chat.api_token",
        help="Bearer token expected by the API (API_TOKEN). Leave empty if the API has none.",
    )
