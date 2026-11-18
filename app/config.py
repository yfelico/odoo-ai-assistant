"""Runtime configuration, read from environment variables and .env."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    odoo_url: str = "http://localhost:8069"
    odoo_db: str = "demo"
    odoo_username: str = "ai_assistant"
    odoo_api_key: SecretStr
    odoo_timeout_seconds: float = 10.0

    # LiteLLM model string, "<provider>/<model>".
    llm_model: str = "gemini/gemini-3.5-flash-lite"
    # Passed explicitly because pydantic-settings does not export .env to os.environ.
    llm_api_key: SecretStr | None = None
    llm_reasoning_effort: Literal["low", "medium", "high"] | None = None
    # LiteLLM retries rate limits and overloads with exponential backoff.
    llm_num_retries: int = Field(default=2, ge=0, le=5)

    agent_max_iterations: int = Field(default=6, ge=1, le=20)
    agent_timeout_seconds: float = Field(default=60.0, gt=0)
    conversation_ttl_seconds: float = Field(default=3600.0, gt=0)
