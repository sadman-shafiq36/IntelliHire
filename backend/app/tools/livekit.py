# the self-hosted livekit server: access tokens for the interview room, closing a room,
# and checking that a webhook really came from it

from datetime import timedelta
from typing import Literal

from livekit import api

from app.config import settings
from app.errors import ExternalServiceError
from app.logging_config import get_logger
from app.tools import http

log = get_logger(__name__)

Kind = Literal["interviewer", "candidate", "transcriber"]
TRANSCRIBER_IDENTITY = "transcriber"


def room_name(interview_id: int) -> str:
    return f"interview-{interview_id}"


def interview_id_from_room(name: str) -> int | None:
    prefix, _, number = name.partition("-")
    return int(number) if prefix == "interview" and number.isdigit() else None


def configured() -> bool:
    return bool(settings.livekit_api_key and settings.livekit_api_secret)


def _require() -> None:
    if not configured():
        raise ExternalServiceError("The video service is not set up. Ask an administrator.")


def participant_token(identity: str, name: str, room: str, kind: Kind) -> str:
    _require()
    grants = api.VideoGrants(room_join=True, room=room, can_subscribe=True)
    if kind == "transcriber":
        # listens and sends transcript lines, never shows up as a tile
        grants.hidden = True
        grants.can_publish = False
        grants.can_publish_data = True
    else:
        grants.can_publish = True
        grants.can_publish_sources = ["camera", "microphone"]
        # only the transcriber sends data, so a candidate can't fake transcript lines
        grants.can_publish_data = False
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grants)
        .with_ttl(timedelta(minutes=settings.livekit_token_minutes))
        .to_jwt()
    )


def _admin_token(room: str) -> str:
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_grants(api.VideoGrants(room_create=True, room_admin=True, room=room))
        .with_ttl(timedelta(minutes=5))
        .to_jwt()
    )


def close_room(room: str) -> bool:
    # disconnects everyone still in it. a room that was never opened is already closed
    _require()
    url = f"{settings.livekit_url.rstrip('/')}/twirp/livekit.RoomService/DeleteRoom"
    with http.client() as c:
        response = c.post(
            url, json={"room": room}, headers={"Authorization": f"Bearer {_admin_token(room)}"}
        )
    if response.status_code == 404:
        return False
    http.check(response, "livekit", "close the room")
    return True


def read_webhook(body: str, authorization: str) -> api.WebhookEvent:
    # the signature covers a hash of the body, so a forged or altered event fails here
    _require()
    receiver = api.WebhookReceiver(
        api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
    )
    return receiver.receive(body, authorization)
