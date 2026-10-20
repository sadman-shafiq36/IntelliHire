import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    shared_secret: str
    allowed_hosts: list[str]
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_tls: bool
    smtp_from_email: str
    smtp_from_name: str
    http_timeout_seconds: float


def load() -> Settings:
    return Settings(
        shared_secret=os.environ.get("MCP_SHARED_SECRET", ""),
        allowed_hosts=[
            h.strip()
            for h in os.environ.get("MCP_ALLOWED_HOSTS", "mcp:8100,localhost:8100").split(",")
            if h.strip()
        ],
        smtp_host=os.environ.get("SMTP_HOST", "mailhog"),
        smtp_port=int(os.environ.get("SMTP_PORT", "1025")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_use_tls=os.environ.get("SMTP_USE_TLS", "false").lower() == "true",
        smtp_from_email=os.environ.get("SMTP_FROM_EMAIL", "no-reply@intellihire.local"),
        smtp_from_name=os.environ.get("SMTP_FROM_NAME", "IntelliHire"),
        http_timeout_seconds=float(os.environ.get("MCP_HTTP_TIMEOUT_SECONDS", "30")),
    )


settings = load()
