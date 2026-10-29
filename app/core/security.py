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


class PasswordTooLongError(ValueError):
    """Raised instead of letting bcrypt ignore everything past 72 bytes."""


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"password must be at most {MAX_PASSWORD_BYTES} bytes, got {len(encoded)}"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


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


def decode_access_token(token: str, *, secret: str) -> dict[str, Any] | None:
    """Returns the claims, or None for any token that is not valid right now.

    One return value for every failure on purpose: expired, wrong signature,
    wrong algorithm and malformed are all "not authenticated", and
    distinguishing them to the caller only helps whoever is probing.

    `algorithms` is pinned to one value. Accepting the token's own `alg` is the
    classic JWT flaw: it lets a caller present an unsigned token and have it
    believed.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
    except InvalidTokenError:
        return None
    return claims
