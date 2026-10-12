# linkedin "share on linkedin" + openid connect. posts go on the connected
# member's own feed; posting into linkedin jobs or as a page needs partner access

import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from app.config import settings
from app.tools.http import ProviderError, check, client, json_or_empty

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
SCOPES = "openid profile w_member_social"

# linkedin "little text" reserved characters, they must be escaped in commentary
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
COMMENTARY_LIMIT = 3000


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_in: int | None
    refresh_token: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class Member:
    id: str
    name: str | None


def is_configured() -> bool:
    return bool(settings.linkedin_client_id and settings.linkedin_client_secret)


def authorize_url(state: str) -> str:
    query = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": SCOPES,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query, quote_via=quote)}"


def exchange_code(code: str) -> TokenSet:
    with client() as http:
        response = http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.linkedin_redirect_uri,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
        )
    data = json_or_empty(check(response, "linkedin", "sign in"))
    return TokenSet(
        access_token=data["access_token"],
        expires_in=data.get("expires_in"),
        refresh_token=data.get("refresh_token"),
        scope=data.get("scope"),
    )


def current_member(access_token: str) -> Member:
    with client() as http:
        response = http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    data = json_or_empty(check(response, "linkedin", "read profile"))
    return Member(id=data["sub"], name=data.get("name"))


def escape(text: str) -> str:
    return _RESERVED.sub(r"\\\1", text)


def hashtag(tag: str) -> str:
    # a real, clickable hashtag in little text format
    clean = re.sub(r"[^A-Za-z0-9]", "", tag)
    return "{hashtag|\\#|" + clean + "}" if clean else ""


def create_post(
    access_token: str,
    member_id: str,
    commentary: str,
    link_url: str,
    link_title: str,
    link_description: str = "",
) -> str:
    # commentary must already be escaped little text. returns the post urn
    body = {
        "author": f"urn:li:person:{member_id}",
        "commentary": commentary[:COMMENTARY_LIMIT],
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "article": {
                "source": link_url,
                "title": link_title[:200],
                "description": link_description[:250],
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    with client() as http:
        response = http.post(
            POSTS_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "LinkedIn-Version": settings.linkedin_api_version,
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
    check(response, "linkedin", "publish post")
    urn = response.headers.get("x-restli-id") or json_or_empty(response).get("id")
    if not urn:
        raise ProviderError(
            "LinkedIn accepted the post but did not say where it is.", provider="linkedin"
        )
    return urn


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"
