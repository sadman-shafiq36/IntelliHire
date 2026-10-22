# reads the mcp servers file. ${NAME} in a
# value is filled from the environment or settings, so the file never holds a secret

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.errors import ExternalServiceError
from app.logging_config import get_logger

log = get_logger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


@dataclass(frozen=True)
class McpServer:
    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)


class McpConfigError(ExternalServiceError):
    # the details go to the log; the interviewer only needs to know it isn't set up
    def __init__(self, reason: str) -> None:
        log.warning("mcp_config_invalid", reason=reason)
        super().__init__("The scheduling service is not set up correctly.")


def _config_path() -> Path:
    path = Path(settings.mcp_servers_file)
    return path if path.is_absolute() else BACKEND_ROOT / path


def _fill(value: str) -> str:
    def lookup(match: re.Match) -> str:
        name = match.group(1)
        if name in os.environ:
            return os.environ[name]
        # values that only live in .env reach the app through settings, not os.environ
        found = getattr(settings, name.lower(), None)
        if found is None:
            raise McpConfigError(f"unknown variable {name}")
        return str(found)

    return _PLACEHOLDER.sub(lookup, value)


def _parse(name: str, raw: Any) -> McpServer:
    if not isinstance(raw, dict):
        raise McpConfigError(f"server {name} is not an object")
    # only remote servers: the backend never starts a local process for a tool
    if raw.get("type", "http") != "http":
        raise McpConfigError(f"server {name} has unsupported type {raw.get('type')}")
    url = raw.get("url")
    if not isinstance(url, str) or not url:
        raise McpConfigError(f"server {name} has no url")
    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        raise McpConfigError(f"server {name} headers must be an object")
    return McpServer(
        name=name,
        url=_fill(url),
        headers={str(k): _fill(str(v)) for k, v in headers.items()},
    )


def load_servers(path: Path | None = None) -> dict[str, McpServer]:
    # read on every call: one background job builds one client, and edits apply without a restart
    path = path or _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise McpConfigError(f"file not found: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"invalid json in {path.name} at line {exc.lineno}") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise McpConfigError("no mcpServers object")
    return {name: _parse(name, raw) for name, raw in servers.items()}


def get_server(name: str, path: Path | None = None) -> McpServer:
    servers = load_servers(path)
    if name not in servers:
        raise McpConfigError(f"no server named {name}")
    return servers[name]
