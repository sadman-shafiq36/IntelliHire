import json
from pathlib import Path

import pytest

from app.tools import mcp_config
from app.tools.mcp_client import McpTools
from app.tools.mcp_config import McpConfigError, get_server, load_servers


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def test_the_shipped_file_has_the_scheduling_server(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_URL", "http://mcp:8100/mcp")
    monkeypatch.setenv("MCP_SHARED_SECRET", "s3cret")
    server = get_server("scheduling")
    assert server.url == "http://mcp:8100/mcp"
    assert server.headers == {"x-mcp-key": "s3cret"}


def test_placeholders_are_filled_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_HOST", "cal.internal")
    path = _write(
        tmp_path,
        {"mcpServers": {"cal": {"url": "http://${CALENDAR_HOST}:9000/mcp", "headers": {}}}},
    )
    assert get_server("cal", path).url == "http://cal.internal:9000/mcp"


def test_a_value_only_in_settings_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_SHARED_SECRET", raising=False)
    monkeypatch.setattr(mcp_config.settings, "mcp_shared_secret", "from-settings")
    path = _write(
        tmp_path,
        {"mcpServers": {"s": {"url": "http://x/mcp", "headers": {"k": "${MCP_SHARED_SECRET}"}}}},
    )
    assert get_server("s", path).headers == {"k": "from-settings"}


def test_type_defaults_to_http_and_several_servers_load(tmp_path):
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "a": {"url": "http://a/mcp"},
                "b": {"type": "http", "url": "http://b/mcp"},
            }
        },
    )
    assert sorted(load_servers(path)) == ["a", "b"]


@pytest.mark.parametrize(
    "data",
    [
        {"mcpServers": {"s": {"url": "http://${NOT_A_REAL_SETTING_XYZ}/mcp"}}},
        {"mcpServers": {"s": {"type": "stdio", "command": "python"}}},
        {"mcpServers": {"s": {"headers": {}}}},
        {"mcpServers": {"s": {"url": "http://x/mcp", "headers": ["x-mcp-key"]}}},
        {"servers": {}},
        "{not json",
    ],
)
def test_bad_files_give_a_plain_message(tmp_path, data):
    path = _write(tmp_path, data)
    with pytest.raises(McpConfigError) as caught:
        load_servers(path)
    assert caught.value.message == "The scheduling service is not set up correctly."


def test_missing_file_and_unknown_server(tmp_path):
    with pytest.raises(McpConfigError):
        load_servers(tmp_path / "nope.json")
    path = _write(tmp_path, {"mcpServers": {"a": {"url": "http://a/mcp"}}})
    with pytest.raises(McpConfigError):
        get_server("b", path)


def test_client_uses_the_configured_url_and_headers(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_URL", "http://elsewhere:8100/mcp")
    monkeypatch.setenv("MCP_SHARED_SECRET", "s3cret")
    tools = McpTools("google-token")
    assert tools._server.url == "http://elsewhere:8100/mcp"
    assert tools._headers() == {"x-mcp-key": "s3cret", "x-google-access-token": "google-token"}
    assert tools.channel == "google"
    assert McpTools()._headers() == {"x-mcp-key": "s3cret"}
