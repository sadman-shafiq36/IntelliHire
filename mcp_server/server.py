# intellihire's mcp server: gmail and google calendar tools for the scheduling agent,
# plus an smtp fallback for when google isn't connected. served over streamable http
# on the compose network only. callers prove themselves with a shared secret, and pass
# the google access token per request, so nothing here stores a credential

import hmac
import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

import google_api
import mailer
from settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# log tool names and outcomes only; arguments carry email addresses and message text
log = logging.getLogger("intellihire.mcp")

mcp = FastMCP(
    "intellihire-scheduling",
    instructions=(
        "Tools to check a Google Calendar's availability, manage the interview event and send "
        "the invitation through Gmail, or through SMTP when Google is not connected. Interviews "
        "are held in IntelliHire's own video room, so no Meet link is ever created."
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=[],
    ),
)


def _google_token(ctx: Context) -> str:
    request = ctx.request_context.request
    token = request.headers.get("x-google-access-token", "") if request else ""
    if not token:
        raise ToolError("Google is not connected, so this tool can't be used.")
    return token


def _run(name: str, fn, *args) -> Any:
    try:
        result = fn(*args)
    except google_api.GoogleError as exc:
        log.warning("tool=%s outcome=google_error status=%s", name, exc.status)
        raise ToolError(str(exc)) from exc
    except OSError as exc:
        log.warning("tool=%s outcome=network_error type=%s", name, type(exc).__name__)
        raise ToolError("The mail or calendar service could not be reached.") from exc
    log.info("tool=%s outcome=ok", name)
    return result


@mcp.tool()
def calendar_free_busy(time_min: str, time_max: str, time_zone: str, ctx: Context) -> dict:
    """Busy periods on the connected Google account's primary calendar.

    time_min and time_max are RFC 3339 date-times; time_zone is an IANA name such as
    Asia/Dhaka. Returns {"busy": [{"start", "end"}, ...]}.
    """
    busy = _run(
        "calendar_free_busy",
        google_api.free_busy,
        _google_token(ctx),
        time_min,
        time_max,
        time_zone,
    )
    return {"busy": busy}


@mcp.tool()
def calendar_create_event(
    summary: str,
    description: str,
    location: str,
    start: str,
    end: str,
    time_zone: str,
    attendees: list[str],
    ctx: Context,
) -> dict:
    """Create an event on the primary calendar.

    No video conference is attached and Google sends no invitation of its own
    (sendUpdates=none). Returns event_id, html_link, start and end.
    """
    return _run(
        "calendar_create_event",
        google_api.create_event,
        _google_token(ctx),
        summary,
        description,
        location,
        start,
        end,
        time_zone,
        attendees,
    )


@mcp.tool()
def calendar_update_event(
    event_id: str,
    start: str,
    end: str,
    time_zone: str,
    ctx: Context,
    description: str | None = None,
) -> dict:
    """Move an existing event to a new time."""
    return _run(
        "calendar_update_event",
        google_api.update_event,
        _google_token(ctx),
        event_id,
        start,
        end,
        time_zone,
        description,
    )


@mcp.tool()
def calendar_delete_event(event_id: str, ctx: Context) -> dict:
    """Delete an event from the primary calendar. Deleting one that is already gone is fine."""
    _run("calendar_delete_event", google_api.delete_event, _google_token(ctx), event_id)
    return {"deleted": True}


@mcp.tool()
def gmail_send(
    to: str,
    subject: str,
    body: str,
    ctx: Context,
    html: str | None = None,
    ics: str | None = None,
    ics_method: str = "REQUEST",
    from_address: str | None = None,
) -> dict:
    """Send an email from the connected Gmail account.

    body is the plain-text version; html, when given, is what most mail apps show. With ics
    the message carries a calendar invitation mail apps offer to add (or, with CANCEL, to
    remove), plus the same file as an attachment. from_address, as
    "Name <address>", sets the sender name; the address must be the connected account's own.
    """
    if ics_method not in ("REQUEST", "CANCEL"):
        raise ToolError("ics_method must be REQUEST or CANCEL.")
    msg = mailer.build_message(to, subject, body, html, ics, ics_method, from_address)
    message_id = _run("gmail_send", google_api.send_message, _google_token(ctx), msg.as_bytes())
    return {"message_id": message_id, "channel": "gmail"}


@mcp.tool()
def smtp_send(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    ics: str | None = None,
    ics_method: str = "REQUEST",
) -> dict:
    """Send the same email over SMTP (MailHog in development) when Google isn't connected."""
    if ics_method not in ("REQUEST", "CANCEL"):
        raise ToolError("ics_method must be REQUEST or CANCEL.")
    msg = mailer.build_message(to, subject, body, html, ics, ics_method)
    message_id = _run("smtp_send", mailer.send_smtp, msg)
    return {"message_id": message_id, "channel": "smtp"}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "tools": len(await mcp.list_tools())})


class SharedSecret:
    # only the backend and worker know the secret; /health stays open for docker
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            given = dict(scope["headers"]).get(b"x-mcp-key", b"").decode()
            if not settings.shared_secret or not hmac.compare_digest(given, settings.shared_secret):
                response = JSONResponse({"error": "not allowed"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = SharedSecret(mcp.streamable_http_app())
