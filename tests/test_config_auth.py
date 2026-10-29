"""Checks for the authentication settings.

Every one of these is a boot-time refusal. A setting that is wrong and
accepted becomes a quiet weakness, and startup is the only moment anyone is
watching.
"""

import pytest
from pydantic import ValidationError

from app.core.config import MIN_JWT_SECRET_BYTES, Settings


def settings(**overrides) -> Settings:
    base = {
        "_env_file": None, "smartcattle_ai_url": None, "ai_api_key": None,
        "database_url": None,
    }
    return Settings(**(base | overrides))


def test_authentication_is_off_without_a_secret():
    # Off rather than broken: a deployment that has not configured it keeps
    # serving the AI ingestion and the dashboard.
    configured = settings()
    assert configured.jwt_secret is None
    assert configured.auth_enabled is False


def test_an_empty_secret_reads_as_unset():
    # An unset Render variable arrives as "", not as absent.
    assert settings(jwt_secret="").auth_enabled is False


def test_a_configured_secret_switches_authentication_on():
    assert settings(jwt_secret="x" * MIN_JWT_SECRET_BYTES).auth_enabled is True


def test_a_short_secret_is_refused():
    # RFC 7518 puts the floor for an HMAC key at the digest size. A shorter one
    # still signs and verifies, so nothing fails -- the tokens are just cheaper
    # to forge.
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        settings(jwt_secret="x" * (MIN_JWT_SECRET_BYTES - 1))


def test_the_refusal_says_how_to_generate_one():
    # Otherwise the realistic response is lengthening a memorable string until
    # the message goes away.
    with pytest.raises(ValidationError, match=r"secrets\.token_urlsafe"):
        settings(jwt_secret="too-short")


def test_the_secret_is_not_in_the_representation():
    # SecretStr. A settings object reaches logs and tracebacks.
    configured = settings(jwt_secret="a-real-looking-secret-0123456789abc")
    assert "a-real-looking-secret" not in repr(configured)
    assert "a-real-looking-secret" not in str(configured)


@pytest.mark.parametrize("minutes", [4, 1441])
def test_an_unreasonable_token_lifetime_is_refused(minutes):
    # There is no refresh token, so this is the whole session: five minutes is
    # unusable and a day is a stolen token that works all day.
    with pytest.raises(ValidationError):
        settings(jwt_secret="x" * 32, access_token_minutes=minutes)


@pytest.mark.parametrize("attempts", [2, 51])
def test_an_unreasonable_attempt_limit_is_refused(attempts):
    with pytest.raises(ValidationError):
        settings(jwt_secret="x" * 32, max_login_attempts=attempts)


@pytest.mark.parametrize("seconds", [29, 3601])
def test_an_unreasonable_lockout_is_refused(seconds):
    with pytest.raises(ValidationError):
        settings(jwt_secret="x" * 32, login_lockout_seconds=seconds)


def test_the_defaults_are_the_documented_ones():
    # README and .env.example both state these; this is what notices when one
    # of them drifts.
    configured = settings(jwt_secret="x" * 32)
    assert configured.access_token_minutes == 60
    assert configured.max_login_attempts == 5
    assert configured.login_lockout_seconds == 300
