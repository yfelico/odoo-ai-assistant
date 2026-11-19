{
    "name": "AI Assistant Chat",
    "summary": "Chat with the AI business assistant from the Odoo top bar",
    "version": "18.0.1.0.0",
    "category": "Sales",
    "license": "LGPL-3",
    "depends": ["web", "base_setup", "sales_team"],
    "data": [
        "data/ir_config_parameter.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ai_assistant_chat/static/src/assistant_systray/*",
        ],
    },
    "installable": True,
}
