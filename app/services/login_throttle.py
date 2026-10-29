"""Login attempt throttling.

Separate from the route because it holds state, and from the user store
because it is not storage: nothing here survives a restart, on purpose.
"""

import math
from datetime import datetime, timedelta
from threading import Lock

from app.core.security import hash_password, verify_password

#: Verified against when no user was found, so a missing address costs the same
#: bcrypt work as a wrong password. Without this, login returns noticeably
#: faster for an address that has no account, and the response time reveals
#: which addresses do.
#:
#: Hashed once at import. The value is irrelevant as long as nothing can
#: present a password that matches it.
_ABSENT_USER_HASH = hash_password("no user with this address exists")


class LoginThrottle:
    """Counts failed logins per address and refuses the address for a while.

    Keyed on the address and not the client IP. The farm staff share a
    connection, so an IP key would lock all of them out together, and an
    attacker with a list of addresses is not slowed by it at all.

    The cost of that choice is worth stating: somebody who knows an address can
    lock its owner out for the lockout window. For a handful of accounts on one
    farm that is the better trade, and it stops being so the moment this serves
    accounts it does not know.
    """

    def __init__(self, max_attempts: int, lockout: timedelta) -> None:
        self._max_attempts = max_attempts
        self._lockout = lockout
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, datetime] = {}
        self._lock = Lock()

    def locked_for(self, email: str, now: datetime) -> int | None:
        """Seconds the caller must wait, or None when the address is not locked."""
        with self._lock:
            until = self._locked_until.get(email)
            if until is None:
                return None
            if now >= until:
                # Expired: forget it, so a lock does not outlive its window.
                del self._locked_until[email]
                self._failures.pop(email, None)
                return None
            # Rounded up, so a Retry-After of 0 never tells the caller to retry
            # immediately into another refusal.
            return max(1, math.ceil((until - now).total_seconds()))

    def verify(self, password: str, password_hash: str | None) -> bool:
        """Checks the password, doing the same work when there is no user."""
        if password_hash is None:
            verify_password(password, _ABSENT_USER_HASH)
            return False
        return verify_password(password, password_hash)

    def record_failure(self, email: str, now: datetime) -> None:
        with self._lock:
            failures = self._failures.get(email, 0) + 1
            self._failures[email] = failures
            if failures >= self._max_attempts:
                self._locked_until[email] = now + self._lockout
                self._failures[email] = 0

    def clear(self, email: str) -> None:
        """Forgets the failures for an address after a successful login."""
        with self._lock:
            self._failures.pop(email, None)
            self._locked_until.pop(email, None)
