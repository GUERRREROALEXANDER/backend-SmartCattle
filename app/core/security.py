"""Password hashing and access tokens.

Kept apart from the routes and the stores because it is the one module where a
mistake is not a bug but a vulnerability, and it should be readable on its
own.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
import jwt
from jwt import InvalidTokenError

#: bcrypt truncates at 72 bytes and silently ignores the rest, so a longer
#: password would be accepted while only its prefix was ever checked. Rejecting
#: it is honest; raising the limit is not possible.
MAX_PASSWORD_BYTES = 72

#: Work factor. Each increment doubles the time to verify. 12 is the common
#: floor in 2026; on Render's free instance it costs roughly a tenth of a
#: second, which is the point -- it is what makes guessing by volume useless.
BCRYPT_ROUNDS = 12

ALGORITHM = "HS256"

#: Tolerance for a token that looks issued in the future. The services run
#: on different machines, so a second of drift must not reject a fresh token.
CLOCK_SKEW_SECONDS = 60


class PasswordTooLongError(ValueError):
    """Raised instead of letting bcrypt ignore everything past 72 bytes."""


def hash_password(password: str, *, rounds: int | None = None) -> str:
    """Hashes a password with bcrypt.

    `rounds` exists for the test suite, which runs at the minimum cost: the
    work factor is what makes hashing slow, and paying it on every fixture
    turns a fast suite into a slow one without testing anything extra. It
    defaults to BCRYPT_ROUNDS so application code cannot weaken it by omission.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"password must be at most {MAX_PASSWORD_BYTES} bytes, got {len(encoded)}"
        )
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS if rounds is None else rounds)
    return bcrypt.hashpw(encoded, salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Checks a password against a stored hash, returning False rather than raising.

    A malformed or truncated hash -- a row written by hand, or a hash from a
    scheme this service does not use -- must read as a failed login, not as a
    500 that tells the caller the account exists.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    *, subject: str, secret: str, expires_in: timedelta, role: str, now: datetime,
) -> str:
    """Signs a short-lived access token.

    `now` is passed in rather than read here so the expiry is derived from the
    same clock as the rest of the request.
    """
    issued_at = now.astimezone(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + expires_in).timestamp()),
        # A unique id per token, so a future revocation list has something to
        # name. Without it two tokens issued in the same second are identical.
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, *, secret: str, now: datetime) -> dict[str, Any] | None:
    """Returns the claims, or None for any token that is not valid at `now`.

    One return value for every failure on purpose: expired, wrong signature,
    wrong algorithm and malformed are all "not authenticated", and
    distinguishing them to the caller only helps whoever is probing.

    `algorithms` is pinned to one value. Accepting the token's own `alg` is the
    classic JWT flaw: it lets a caller present an unsigned token and have it
    believed.

    The time claims are checked here against `now` rather than left to PyJWT,
    which reads the system clock. create_access_token already takes the
    application's injected clock, and the two halves have to agree: a token
    minted against a moved clock must verify against that same clock, or
    issuing and accepting disagree inside one request.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={
                "require": ["sub", "exp", "iat"],
                # Checked below against `now`, not the system clock.
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except InvalidTokenError:
        return None

    moment = now.astimezone(UTC).timestamp()
    try:
        expires_at = float(claims["exp"])
        issued_at = float(claims["iat"])
    except (KeyError, TypeError, ValueError):
        return None

    if moment >= expires_at:
        return None
    # A token issued in the future is not yet valid. CLOCK_SKEW_SECONDS of
    # tolerance because the AI service and this backend run on different
    # machines, and a second of drift must not reject a fresh token.
    if issued_at - moment > CLOCK_SKEW_SECONDS:
        return None
    return claims
