# google oauth, forms and drive. the forms api can't create file-upload questions,
# so each job's form is a drive copy of a template the user built by hand

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from app.config import settings
from app.tools.http import ProviderError, check, client, json_or_empty

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
FORMS_URL = "https://forms.googleapis.com/v1/forms"
DRIVE_URL = "https://www.googleapis.com/drive/v3/files"

# the same google connection sends interview invitations and books the calendar event,
# through the mcp server. asking for these at connect time means one consent screen
SCHEDULING_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
)

# full drive scope is needed to copy a template the app didn't create itself
SCOPES = " ".join(
    [
        "openid",
        "email",
        "https://www.googleapis.com/auth/forms.body",
        "https://www.googleapis.com/auth/forms.responses.readonly",
        "https://www.googleapis.com/auth/drive",
        *SCHEDULING_SCOPES,
    ]
)


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_in: int | None
    refresh_token: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class FormRef:
    form_id: str
    responder_url: str
    edit_url: str


@dataclass(frozen=True)
class Question:
    question_id: str
    title: str
    kind: str  # text | file | choice | other


@dataclass
class FormResponse:
    response_id: str
    submitted_at: str | None
    respondent_email: str | None
    # question id -> list of text values
    text: dict[str, list[str]] = field(default_factory=dict)
    # question id -> list of {fileId, fileName, mimeType}
    files: dict[str, list[dict[str, str]]] = field(default_factory=dict)


def is_configured() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def authorize_url(state: str) -> str:
    query = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # offline + consent so google returns a refresh token every time
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query, quote_via=quote)}"


def _token_request(data: dict[str, str], action: str) -> TokenSet:
    with client() as http:
        response = http.post(TOKEN_URL, data=data)
    body = json_or_empty(check(response, "google", action))
    return TokenSet(
        access_token=body["access_token"],
        expires_in=body.get("expires_in"),
        refresh_token=body.get("refresh_token"),
        scope=body.get("scope"),
    )


def exchange_code(code: str) -> TokenSet:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
        },
        "sign in",
    )


def refresh(refresh_token: str) -> TokenSet:
    try:
        return _token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
            },
            "refresh sign-in",
        )
    except ProviderError as exc:
        # a revoked or expired refresh token comes back as 400 invalid_grant; retrying never helps
        if exc.status == 400:
            raise ProviderError(
                "The Google connection has expired. Reconnect Google in Settings → Integrations.",
                status=400,
                provider="google",
            ) from exc
        raise


def current_user(access_token: str) -> tuple[str, str | None]:
    with client() as http:
        response = http.get(USERINFO_URL, headers=_auth(access_token))
    data = json_or_empty(check(response, "google", "read account"))
    return data["email"], data.get("name")


def copy_form(access_token: str, template_id: str, name: str) -> str:
    with client() as http:
        response = http.post(
            f"{DRIVE_URL}/{template_id}/copy",
            params={"fields": "id"},
            json={"name": name},
            headers=_auth(access_token),
        )
    return json_or_empty(check(response, "google", "copy the template form"))["id"]


def update_form_info(access_token: str, form_id: str, title: str, description: str) -> None:
    body = {
        "requests": [
            {
                "updateFormInfo": {
                    "info": {"title": title, "description": description},
                    "updateMask": "title,description",
                }
            }
        ]
    }
    with client() as http:
        response = http.post(
            f"{FORMS_URL}/{form_id}:batchUpdate", json=body, headers=_auth(access_token)
        )
    check(response, "google", "update the form")


def set_accepting_responses(access_token: str, form_id: str, accepting: bool) -> None:
    body = {
        "publishSettings": {
            "publishState": {"isPublished": True, "isAcceptingResponses": accepting}
        },
        "updateMask": "publishState",
    }
    with client() as http:
        response = http.post(
            f"{FORMS_URL}/{form_id}:setPublishSettings", json=body, headers=_auth(access_token)
        )
    check(response, "google", "open the form" if accepting else "close the form")


def get_form(access_token: str, form_id: str) -> tuple[FormRef, list[Question]]:
    with client() as http:
        response = http.get(f"{FORMS_URL}/{form_id}", headers=_auth(access_token))
    data = json_or_empty(check(response, "google", "read the form"))
    ref = FormRef(
        form_id=data["formId"],
        responder_url=data.get("responderUri", ""),
        edit_url=f"https://docs.google.com/forms/d/{data['formId']}/edit",
    )
    questions: list[Question] = []
    for item in data.get("items", []):
        question = (item.get("questionItem") or {}).get("question")
        if not question:
            continue
        if "fileUploadQuestion" in question:
            kind = "file"
        elif "textQuestion" in question:
            kind = "text"
        elif "choiceQuestion" in question:
            kind = "choice"
        else:
            kind = "other"
        questions.append(Question(question["questionId"], item.get("title", ""), kind))
    return ref, questions


def list_responses(access_token: str, form_id: str) -> list[FormResponse]:
    responses: list[FormResponse] = []
    page_token: str | None = None
    with client() as http:
        while True:
            params = {"pageSize": 500}
            if page_token:
                params["pageToken"] = page_token
            response = http.get(
                f"{FORMS_URL}/{form_id}/responses", params=params, headers=_auth(access_token)
            )
            data = json_or_empty(check(response, "google", "read the form responses"))
            for raw in data.get("responses", []):
                responses.append(_parse_response(raw))
            page_token = data.get("nextPageToken")
            if not page_token:
                return responses


def _parse_response(raw: dict[str, Any]) -> FormResponse:
    parsed = FormResponse(
        response_id=raw["responseId"],
        submitted_at=raw.get("lastSubmittedTime") or raw.get("createTime"),
        respondent_email=raw.get("respondentEmail"),
    )
    for question_id, answer in (raw.get("answers") or {}).items():
        if "fileUploadAnswers" in answer:
            parsed.files[question_id] = answer["fileUploadAnswers"].get("answers", [])
        elif "textAnswers" in answer:
            parsed.text[question_id] = [
                a.get("value", "") for a in answer["textAnswers"].get("answers", [])
            ]
    return parsed


def file_metadata(access_token: str, file_id: str) -> dict[str, Any]:
    with client() as http:
        response = http.get(
            f"{DRIVE_URL}/{file_id}",
            params={"fields": "id,name,mimeType,size"},
            headers=_auth(access_token),
        )
    return json_or_empty(check(response, "google", "read the CV file details"))


def download_file(access_token: str, file_id: str, max_bytes: int) -> bytes:
    with (
        client() as http,
        http.stream(
            "GET", f"{DRIVE_URL}/{file_id}", params={"alt": "media"}, headers=_auth(access_token)
        ) as response,
    ):
        if not response.is_success:
            response.read()
            check(response, "google", "download the CV")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ProviderError("The CV file is larger than allowed.", provider="google")
            chunks.append(chunk)
    return b"".join(chunks)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
