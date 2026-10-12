import time

import pytest

from app.errors import AuthenticationError
from app.services.security import (
    create_access_token,
    decode_access_token,
    generate_join_token,
    hash_password,
    verify_password,
)


def test_password_round_trip():
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed)


def test_wrong_password_is_rejected():
    hashed = hash_password("correct-horse-battery")
    assert not verify_password("wrong-password", hashed)


def test_hash_is_salted():
    # same hash twice would reveal two accounts sharing a password
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_password_survives_a_corrupt_hash():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_password_longer_than_bcrypt_limit_is_rejected():
    # bcrypt truncates at 72 bytes, longer passwords would collide
    with pytest.raises(ValueError):
        hash_password("a" * 73)


def test_token_round_trip():
    token = create_access_token(subject="42", role="interviewer")
    claims = decode_access_token(token)
    assert claims["sub"] == "42"
    assert claims["role"] == "interviewer"


def test_token_carries_no_personal_data():
    # a jwt is readable by anyone holding it
    token = create_access_token(subject="42", role="interviewer")
    claims = decode_access_token(token)
    assert set(claims) == {"sub", "role", "iat", "exp"}


def test_expired_token_is_rejected():
    token = create_access_token(subject="42", role="interviewer", expires_minutes=-1)
    time.sleep(0.01)
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_tampered_token_is_rejected():
    token = create_access_token(subject="42", role="candidate")
    # flip a character in the payload segment
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-2]}XY.{signature}"
    with pytest.raises(AuthenticationError):
        decode_access_token(tampered)


def test_join_tokens_are_unguessable_and_unique():
    tokens = {generate_join_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) >= 40 for token in tokens)
