from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    database_path: Path = Path("data/betting_app.db")
    database_url: str = ""
    database_auth_token: str = ""
    app_currency: str = "GBP"
    app_timezone: str = "Europe/London"
    admin_password: str = ""
    viewer_password: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def prepare_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings
