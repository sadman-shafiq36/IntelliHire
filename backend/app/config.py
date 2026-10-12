from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    api_port: int = 8000
    public_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    postgres_user: str = "intellihire"
    postgres_password: str = ""
    postgres_db: str = "intellihire"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_host: str = "redis"
    redis_port: int = 6379

    chroma_host: str = "chroma"
    chroma_port: int = 8000

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    google_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_models: str = "gemini-3.1-flash-lite,gemini-flash-latest"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    llm_temperature: float = 0.0
    llm_max_retries: int = 3

    whisper_service_url: str = "http://whisper:9000"
    whisper_model_size: str = "base.en"
    whisper_compute_type: str = "int8"
    audio_chunk_seconds: int = 4

    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_from_email: str = "no-reply@intellihire.local"
    smtp_from_name: str = "IntelliHire"

    storage_dir: str = "/data/storage"
    max_upload_mb: int = 10
    allowed_cv_extensions: str = ".pdf,.docx"
    allowed_policy_extensions: str = ".pdf,.docx,.txt,.md"
    policy_chunk_chars: int = 1200
    policy_chunk_overlap: int = 200
    policy_min_score: float = 0.55

    use_fake_ai: bool = Field(default=False)

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_cv_extension_list(self) -> list[str]:
        return [ext.strip().lower() for ext in self.allowed_cv_extensions.split(",") if ext.strip()]

    @property
    def gemini_fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]

    @property
    def allowed_policy_extension_list(self) -> list[str]:
        return [
            ext.strip().lower() for ext in self.allowed_policy_extensions.split(",") if ext.strip()
        ]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
