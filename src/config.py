from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    GEMINI_API_KEY: str = Field(
        default=" PLACEHOLDER_API_KEY ",
        description="Google Gemini API Key for LLM & Agent reasoning",
    )
    QDRANT_URL: str = Field(
        default="http://localhost:6333",
        description="URL endpoint for Qdrant vector database",
    )
    POSTGRES_URI: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/agent_platform",
        description="PostgreSQL connection URI for LangGraph checkpointer and persistence",
    )
    TARGET_REPO_PATH: Path = Field(
        default=Path("./target_repo"),
        description="Local filesystem path to the target repository being analyzed and modified",
    )

    @field_validator("TARGET_REPO_PATH", mode="before")
    @classmethod
    def resolve_target_repo_path(cls, value: str | Path) -> Path:
        path = Path(value)
        return path.resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()


settings = get_settings()

