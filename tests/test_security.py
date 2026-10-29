from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import (
    ALGORITHM,
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SECRET = "a-secret-long-enough-for-hs256-0123"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def test_hash_is_not_the_password():
    hashed = hash_password("una-clave-larga")
    assert "una-clave-larga" not in hashed
    # bcrypt's own prefix, so a change of algorithm is visible here.
    assert hashed.startswith("$2b$")


def test_same_password_hashes_differently():
    # Per-row salt. Two accounts with the same password must not share a hash,
    # or a leaked table shows at a glance which accounts to attack together.
    assert hash_password("una-clave-larga") != hash_password("una-clave-larga")


def test_verify_accepts_the_password_and_rejects_others():
    hashed = hash_password("una-clave-larga")
    assert verify_password("una-clave-larga", hashed)
    assert not verify_password("una-clave-largo", hashed)
    assert not verify_password("", hashed)


def test_password_at_the_bcrypt_limit_is_accepted():
    at_limit = "a" * MAX_PASSWORD_BYTES
    assert verify_password(at_limit, hash_password(at_limit))


def test_password_over_the_bcrypt_limit_is_refused():
    # bcrypt ignores everything past 72 bytes, so accepting a longer password
    # would check only its prefix: two different passwords sharing 72 bytes
    # would both open the account.
    with pytest.raises(PasswordTooLongError):
        hash_password("a" * (MAX_PASSWORD_BYTES + 1))


def test_accented_characters_count_as_two_bytes():
    # The staff write in Spanish. 40 accented characters are 80 bytes, over the
    # limit, even though the string is well under 72 characters.
    with pytest.raises(PasswordTooLongError):
        hash_password("ñ" * 40)


def test_verify_rejects_an_overlong_password_without_raising():
    # On the verify path an overlong password is a failed login, not an error:
    # raising would turn a probe into a 500.
    assert not verify_password("a" * 200, hash_password("una-clave-larga"))


@pytest.mark.parametrize("broken", ["", "not-a-hash", "$2b$12$short", "$1$md5$x"])
def test_verify_rejects_a_malformed_hash_without_raising(broken):
    # A row written by hand, or a hash from a scheme this service does not use,
    # has to read as a failed login rather than a 500 that confirms the account
    # exists.
    assert not verify_password("una-clave-larga", broken)


def test_token_carries_the_subject_and_role():
    token = create_access_token(
        subject="7", secret=SECRET, expires_in=timedelta(minutes=60), role="owner", now=NOW,
    )
    claims = decode_access_token(token, secret=SECRET)
    assert claims is not None
    assert claims["sub"] == "7"
    assert claims["role"] == "owner"
    assert claims["exp"] == int((NOW + timedelta(minutes=60)).timestamp())


def test_each_token_has_its_own_id():
    # Two tokens issued at the same instant for the same user must differ, so a
    # future revocation list has something to name.
    first, second = (
        create_access_token(
            subject="7", secret=SECRET, expires_in=timedelta(minutes=60), role="owner", now=NOW,
        )
        for _ in range(2)
    )
    assert first != second
    assert decode_access_token(first, secret=SECRET)["jti"] != decode_access_token(second, secret=SECRET)["jti"]


def test_expired_token_is_rejected():
    token = create_access_token(
        subject="7", secret=SECRET, expires_in=timedelta(minutes=-1), role="owner", now=NOW,
    )
    assert decode_access_token(token, secret=SECRET) is None


def test_token_signed_with_another_secret_is_rejected():
    token = create_access_token(
        subject="7", secret="a-different-secret-also-long-enough", expires_in=timedelta(minutes=60),
        role="owner", now=NOW,
    )
    assert decode_access_token(token, secret=SECRET) is None


def test_unsigned_token_is_rejected():
    # The classic JWT flaw: alg=none, presented as if signed. Rejected because
    # decode pins the algorithm instead of reading it from the token.
    forged = jwt.encode({"sub": "7", "role": "owner"}, key="", algorithm="none")
    assert decode_access_token(forged, secret=SECRET) is None


def test_token_without_a_subject_is_rejected():
    # Signed by us, so the signature is valid, and still unusable: there is no
    # account to resolve it to.
    token = jwt.encode(
        {"role": "owner", "iat": int(NOW.timestamp()),
         "exp": int((NOW + timedelta(minutes=60)).timestamp())},
        SECRET, algorithm=ALGORITHM,
    )
    assert decode_access_token(token, secret=SECRET) is None


@pytest.mark.parametrize("junk", ["", "nope", "a.b.c", "Bearer x"])
def test_malformed_token_is_rejected(junk):
    assert decode_access_token(junk, secret=SECRET) is None
