# the scheduling agent's way to an mcp server (gmail, google calendar, smtp). servers are
# listed in mcp_servers.json, see tools/mcp_config.py. one short mcp session per tool call:
# the server is stateless, and graph nodes are plain functions

import asyncio
import json
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.errors import ExternalServiceError
from app.logging_config import get_logger
from app.tools.mcp_config import get_server

log = get_logger(__name__)


class SchedulingTools(Protocol):
    # what the scheduling graph needs; tests pass an in-memory fake
    channel: str

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class ToolCallError(ExternalServiceError):
    # message comes from the mcp server and is written for the interviewer
    pass


class McpTools:
    def __init__(
        self,
        google_token: str | None = None,
        mail_from: str | None = None,
        server: str = "scheduling",
    ) -> None:
        # the token lives on this object for one run and is never put in graph state,
        # so it can't end up in a checkpoint
        self._token = google_token
        self._mail_from = mail_from
        self._server = get_server(server)
        self.channel = "google" if google_token else "smtp"

    def _headers(self) -> dict[str, str]:
        headers = dict(self._server.headers)
        if self._token:
            headers["x-google-access-token"] = self._token
        return headers

    async def _call(self, name: str, arguments: dict[str, Any]):
        async with (
            httpx.AsyncClient(headers=self._headers(), timeout=60) as http,
            streamable_http_client(self._server.url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await session.call_tool(name, arguments)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # without a from header gmail shows the google account's own name
        if name == "gmail_send" and self._mail_from:
            arguments = {**arguments, "from_address": self._mail_from}
        try:
            result = asyncio.run(self._call(name, arguments))
        except Exception as exc:
            log.warning("mcp_call_failed", tool=name, error_type=type(exc).__name__)
            raise ToolCallError(
                "The scheduling service could not be reached. Please try again shortly."
            ) from exc
        text = result.content[0].text if result.content else ""
        if result.isError:
            log.warning("mcp_tool_error", tool=name)
            raise ToolCallError(text or "The scheduling tool reported an error.")
        log.info("mcp_tool_ok", tool=name)
        return json.loads(text) if text else {}

    def list_tools(self) -> list[str]:
        async def run():
            async with (
                httpx.AsyncClient(headers=self._headers(), timeout=30) as http,
                streamable_http_client(self._server.url, http_client=http) as (r, w, _),
                ClientSession(r, w) as session,
            ):
                await session.initialize()
                return [t.name for t in (await session.list_tools()).tools]

        return asyncio.run(run())
