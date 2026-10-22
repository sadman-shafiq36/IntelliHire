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
    allow_registration: bool = True

    google_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_models: str = "gemini-3.1-flash-lite,gemini-flash-latest"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    llm_temperature: float = 0.0
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 120

    whisper_service_url: str = "http://whisper:9000"
    whisper_model_size: str = "base.en"
    whisper_compute_type: str = "int8"

    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_requests_per_minute: int = 18
    llm_providers: str = "groq,gemini"
    groq_llm_models: str = "openai/gpt-oss-120b,openai/gpt-oss-20b"
    transcription_language: str = "en"
    speech_silence_ms: int = 700
    speech_max_seconds: int = 15
    speech_min_ms: int = 300

    livekit_url: str = "http://livekit:7880"
    livekit_public_url: str = "ws://localhost:7880"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_token_minutes: int = 180
    transcriber_poll_seconds: float = 2.0
    session_idle_end_minutes: int = 30

    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_from_email: str = "no-reply@intellihire.local"
    smtp_from_name: str = "IntelliHire"

    email_sender_name: str = ""
    email_sender_title: str = ""
    email_sender_company: str = ""

    mcp_server_url: str = "http://mcp:8100/mcp"
    mcp_shared_secret: str = ""
    mcp_servers_file: str = "mcp_servers.json"
    interview_link_valid_days: int = 1
    scheduling_work_start_hour: int = 9
    scheduling_work_end_hour: int = 17
    scheduling_search_days: int = 14

    storage_dir: str = "/data/storage"
    max_upload_mb: int = 10
    allowed_cv_extensions: str = ".pdf,.docx"
    allowed_policy_extensions: str = ".pdf,.docx,.txt,.md"
    policy_chunk_chars: int = 1200
    policy_chunk_overlap: int = 200
    policy_min_score: float = 0.55
    screening_concurrency: int = 3
    auto_sync_minutes: int = 15
    copilot_discussed_similarity: float = 0.78

    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/api/v1/integrations/linkedin/callback"
    linkedin_api_version: str = "202609"

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/integrations/google/callback"
    google_form_template_id: str = ""

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
    def llm_provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.llm_providers.split(",") if p.strip()]

    @property
    def groq_llm_model_list(self) -> list[str]:
        return [m.strip() for m in self.groq_llm_models.split(",") if m.strip()]

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
