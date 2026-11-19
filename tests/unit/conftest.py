from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.api.routes import get_settings
from app.config import Settings
from app.main import app


@pytest.fixture(autouse=True)
def _settings_without_local_env() -> Iterator[None]:
    """Keeps API unit tests independent of the local .env.

    /chat and /confirm read the settings to check API_TOKEN: without this, a token
    in .env would make them answer 401, and a missing .env (as in CI) would fail on
    the required Odoo key. Tests that need a token override get_settings themselves.

    Values are passed explicitly: importing LiteLLM loads .env into os.environ, and
    environment variables would otherwise still reach these settings.
    """
    settings = Settings(_env_file=None, odoo_api_key=SecretStr("unused"), api_token=None)
    app.dependency_overrides[get_settings] = lambda: settings
    yield
    app.dependency_overrides.pop(get_settings, None)
