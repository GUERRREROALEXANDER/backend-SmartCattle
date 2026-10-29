"""Unit checks for the login throttle.

Tested directly as well as through the endpoint, because its two interesting
properties -- the constant-time miss and the expiry -- are awkward to observe
from outside and easy to break from inside.
"""

from datetime import UTC, datetime, timedelta

from app.core.security import hash_password
from app.services.login_throttle import LoginThrottle

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def throttle(max_attempts: int = 3, seconds: int = 300) -> LoginThrottle:
    return LoginThrottle(max_attempts=max_attempts, lockout=timedelta(seconds=seconds))


def test_a_fresh_address_is_not_locked():
    assert throttle().locked_for("ana@finca.example", NOW) is None


def test_the_lock_engages_on_the_configured_attempt():
    limiter = throttle(max_attempts=3)
    for _ in range(2):
        limiter.record_failure("ana@finca.example", NOW)
    # Two failures with a limit of three: still open.
    assert limiter.locked_for("ana@finca.example", NOW) is None

    limiter.record_failure("ana@finca.example", NOW)
    assert limiter.locked_for("ana@finca.example", NOW) is not None


def test_retry_after_is_never_zero():
    limiter = throttle(max_attempts=1, seconds=10)
    limiter.record_failure("ana@finca.example", NOW)
    # Half a second left. Rounded up, because a Retry-After of 0 tells the
    # caller to retry immediately into another refusal.
    remaining = limiter.locked_for("ana@finca.example", NOW + timedelta(seconds=9.5))
    assert remaining == 1


def test_retry_after_counts_down():
    limiter = throttle(max_attempts=1, seconds=300)
    limiter.record_failure("ana@finca.example", NOW)
    assert limiter.locked_for("ana@finca.example", NOW) == 300
    assert limiter.locked_for("ana@finca.example", NOW + timedelta(seconds=100)) == 200


def test_the_lock_expires_and_is_forgotten():
    limiter = throttle(max_attempts=1, seconds=60)
    limiter.record_failure("ana@finca.example", NOW)
    assert limiter.locked_for("ana@finca.example", NOW + timedelta(seconds=61)) is None
    # And the tally is cleared with it, so the next mistake does not land on a
    # full count from an hour ago.
    limiter.record_failure("ana@finca.example", NOW + timedelta(seconds=61))
    assert limiter.locked_for("ana@finca.example", NOW + timedelta(seconds=62)) is not None


def test_one_address_does_not_lock_another():
    limiter = throttle(max_attempts=1)
    limiter.record_failure("ana@finca.example", NOW)
    assert limiter.locked_for("ana@finca.example", NOW) is not None
    # Keyed per address: the farm staff share a connection, so an IP key would
    # lock all of them out together.
    assert limiter.locked_for("beto@finca.example", NOW) is None


def test_clearing_forgets_both_the_tally_and_the_lock():
    limiter = throttle(max_attempts=1)
    limiter.record_failure("ana@finca.example", NOW)
    limiter.clear("ana@finca.example")
    assert limiter.locked_for("ana@finca.example", NOW) is None


def test_verify_accepts_the_right_password():
    stored = hash_password("una-clave-larga")
    assert throttle().verify("una-clave-larga", stored) is True
    assert throttle().verify("incorrecta", stored) is False


def test_verify_without_a_user_still_does_the_work():
    """The constant-time miss.

    Returns False for an absent user, and verifies against a throwaway hash to
    get there, so a missing address costs the same bcrypt work as a wrong
    password. Without it, login returns in microseconds for an address with no
    account and in a tenth of a second for one that has -- and the response
    time reveals what the shared 401 message is there to hide.
    """
    from app.services import login_throttle

    calls = []
    original = login_throttle.verify_password

    def counting(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return original(password, password_hash)

    login_throttle.verify_password = counting  # type: ignore[assignment]
    try:
        assert throttle().verify("cualquiera", None) is False
    finally:
        login_throttle.verify_password = original  # type: ignore[assignment]

    # It really hashed something rather than short-circuiting.
    assert len(calls) == 1
    assert calls[0].startswith("$2b$")
