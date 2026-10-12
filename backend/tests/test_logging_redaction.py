from app.logging_config import REDACTED, redact_processor


def redact(event: dict) -> dict:
    return redact_processor(None, "info", event)


def test_sensitive_keys_are_blanked():
    result = redact(
        {
            "event": "login",
            "password": "hunter2",
            "google_api_key": "AIza-real-key",
            "token": "ey.jwt.value",
        }
    )
    assert result["password"] == REDACTED
    assert result["google_api_key"] == REDACTED
    assert result["token"] == REDACTED


def test_personal_fields_are_blanked():
    result = redact({"event": "cv_parsed", "full_name": "Jane Smith", "phone": "555-0100"})
    assert result["full_name"] == REDACTED
    assert result["phone"] == REDACTED


def test_key_matching_ignores_case():
    assert redact({"Password": "hunter2"})["Password"] == REDACTED


def test_emails_are_masked_even_inside_a_message():
    result = redact({"event": "sent invitation to jane.smith@example.com for review"})
    assert "jane.smith@example.com" not in result["event"]
    assert REDACTED in result["event"]


def test_harmless_identifiers_survive():
    # redaction shouldn't be so broad that logs become useless
    result = redact({"event": "cv_parsed", "cv_id": 42, "job_id": 7, "pages": 3})
    assert result["cv_id"] == 42
    assert result["job_id"] == 7
    assert result["event"] == "cv_parsed"
