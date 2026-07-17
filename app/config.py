"""
app/config.py — centralised settings via pydantic-settings.
All other modules import `settings` from here; never read os.environ directly.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # SQLite
    DATABASE_URL: str = "sqlite:///./data/ct200.db"

    # MongoDB
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "ct200_llm"

    # Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"


settings = Settings()
