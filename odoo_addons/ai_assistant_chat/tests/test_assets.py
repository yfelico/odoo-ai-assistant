from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAssets(TransactionCase):
    def test_backend_styles_compile_with_the_chat_panel(self):
        # A Sass error in one module breaks the styles of the whole backend.
        bundle = self.env["ir.qweb"]._get_asset_bundle("web.assets_web", js=False)

        # preprocess_css always compiles; css() could return a cached attachment.
        bundle.preprocess_css()

        self.assertEqual(bundle.css_errors, [])
        self.assertTrue(
            # Inline assets (e.g. hoisted @-rules) have no url.
            any("ai_assistant_chat" in (asset.url or "") for asset in bundle.stylesheets),
            "the chat panel styles are not in the backend bundle",
        )
