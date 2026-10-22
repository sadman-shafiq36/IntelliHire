# an in-memory stand-in for the linkedin and google apis, served through
# httpx.MockTransport so the integration code runs unchanged and offline

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

PDF_BYTES = b"%PDF-1.4 fake cv content"


@dataclass
class FakeProviders:
    template_id: str = "template-123"
    template_has_file_question: bool = True
    linkedin_post_status: int = 201
    responses: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    post_headers: list[dict[str, str]] = field(default_factory=list)
    form_info: dict[str, Any] = field(default_factory=dict)
    accepting: dict[str, bool] = field(default_factory=dict)
    google_refreshes: int = 0
    google_refresh_revoked: bool = False
    copies: int = 0

    def add_response(
        self,
        response_id: str,
        email: str | None = "ada@example.com",
        name: str | None = "Ada Lovelace",
        cv: tuple[str, str, bytes] | None = ("ada-cv.pdf", "application/pdf", PDF_BYTES),
        consent: bool = True,
        extra: dict[str, str] | None = None,
    ) -> None:
        answers: dict[str, Any] = {}
        if name:
            answers["q_name"] = _text(name)
        if consent:
            answers["q_consent"] = _text("I agree")
        for qid, value in (extra or {}).items():
            answers[qid] = _text(value)
        if cv:
            file_id = f"file-{response_id}"
            self.files[file_id] = {"name": cv[0], "mimeType": cv[1], "data": cv[2]}
            answers["q_cv"] = {
                "fileUploadAnswers": {
                    "answers": [{"fileId": file_id, "fileName": cv[0], "mimeType": cv[1]}]
                }
            }
        self.responses.append(
            {
                "responseId": response_id,
                "lastSubmittedTime": "2026-10-12T09:30:00.000Z",
                "respondentEmail": email,
                "answers": answers,
            }
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        self.calls.append((request.method, f"{url.host}{path}"))

        # --- linkedin
        if url.host == "www.linkedin.com" and path == "/oauth/v2/accessToken":
            return httpx.Response(
                200,
                json={
                    "access_token": "li-token",
                    "expires_in": 5184000,
                    "scope": "w_member_social",
                },
            )
        if url.host == "api.linkedin.com" and path == "/v2/userinfo":
            return _authed(request, "li-token") or httpx.Response(
                200, json={"sub": "member-42", "name": "Test Member"}
            )
        if url.host == "api.linkedin.com" and path == "/rest/posts":
            if denied := _authed(request, "li-token"):
                return denied
            if self.linkedin_post_status != 201:
                return httpx.Response(self.linkedin_post_status, json={"message": "nope"})
            self.posts.append(json.loads(request.content))
            self.post_headers.append(dict(request.headers))
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:999"})

        # --- google oauth
        if url.host == "oauth2.googleapis.com" and path == "/token":
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["refresh_token"]:
                self.google_refreshes += 1
                if self.google_refresh_revoked:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3599})
            return httpx.Response(
                200,
                json={"access_token": "g-token", "expires_in": 3599, "refresh_token": "g-refresh"},
            )
        if url.host == "openidconnect.googleapis.com":
            return httpx.Response(200, json={"email": "owner@example.com"})

        if (url.host in ("www.googleapis.com", "forms.googleapis.com")) and (
            denied := _authed(request, "g-token")
        ):
            return denied

        # --- drive
        if url.host == "www.googleapis.com" and path == f"/drive/v3/files/{self.template_id}/copy":
            self.copies += 1
            return httpx.Response(200, json={"id": "form-1"})
        if url.host == "www.googleapis.com" and path.startswith("/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[1]
            file = self.files.get(file_id)
            if file is None:
                return httpx.Response(404, json={})
            if url.params.get("alt") == "media":
                return httpx.Response(200, content=file["data"])
            return httpx.Response(
                200,
                json={
                    "id": file_id,
                    "name": file["name"],
                    "mimeType": file["mimeType"],
                    "size": str(len(file["data"])),
                },
            )

        # --- forms
        if url.host == "forms.googleapis.com":
            if path == "/v1/forms/form-1:batchUpdate":
                body = json.loads(request.content)
                self.form_info = body["requests"][0]["updateFormInfo"]["info"]
                return httpx.Response(200, json={})
            if path == "/v1/forms/form-1:setPublishSettings":
                body = json.loads(request.content)
                state = body["publishSettings"]["publishState"]["isAcceptingResponses"]
                self.accepting["form-1"] = state
                return httpx.Response(200, json={})
            if path == "/v1/forms/form-1":
                return httpx.Response(200, json=self._form())
            if path == "/v1/forms/form-1/responses":
                return httpx.Response(200, json={"responses": self.responses})

        return httpx.Response(404, json={"error": f"unexpected {request.method} {url}"})

    def _form(self) -> dict[str, Any]:
        items = [
            _question("q_name", "Full name", "textQuestion"),
            _question("q_phone", "Phone number", "textQuestion"),
            _question("q_location", "Location", "textQuestion"),
            _question("q_exp", "Years of professional experience", "textQuestion"),
            _question("q_li", "LinkedIn profile URL", "textQuestion"),
            _question("q_cover", "Cover note", "textQuestion"),
            _question(
                "q_consent",
                "I agree that my application and CV are processed to assess me",
                "choiceQuestion",
            ),
        ]
        if self.template_has_file_question:
            items.append(_question("q_cv", "Upload your CV", "fileUploadQuestion"))
        return {
            "formId": "form-1",
            "responderUri": "https://docs.google.com/forms/d/e/form-1-public/viewform",
            "items": items,
        }


def _question(qid: str, title: str, kind: str) -> dict[str, Any]:
    return {"title": title, "questionItem": {"question": {"questionId": qid, kind: {}}}}


def _text(value: str) -> dict[str, Any]:
    return {"textAnswers": {"answers": [{"value": value}]}}


def _authed(request: httpx.Request, token: str) -> httpx.Response | None:
    if request.headers.get("Authorization") != f"Bearer {token}":
        return httpx.Response(401, json={"error": "invalid token"})
    return None
