"""Runtime configuration, read from environment variables and .env."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    odoo_url: str = "http://localhost:8069"
    odoo_db: str = "demo"
    odoo_username: str = "ai_assistant"
    odoo_api_key: SecretStr
    odoo_timeout_seconds: float = 10.0
