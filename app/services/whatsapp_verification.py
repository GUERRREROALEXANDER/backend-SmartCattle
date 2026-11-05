"""Codes that prove someone holds the WhatsApp number they typed.

Without this, a typo sends a farm's alerts to a stranger for as long as nobody
notices. Kept in memory, like the QR codes: a restart only means asking again.
"""

import secrets
from datetime import datetime, timedelta
from threading import Lock

LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5
#: The limits also protect the number's holder: anyone can type a stranger's number, and each request messages it.
COOLDOWN = timedelta(seconds=60)
HOURLY_LIMIT = 5
HOUR = timedelta(hours=1)


class TooManyCodesError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("too many verification codes")
        self.retry_after = retry_after


class PhoneVerifications:
    def __init__(self) -> None:
        self._waiting: dict[int, tuple[str, str, datetime, int]] = {}
        self._sent: dict[str, list[datetime]] = {}
        self._lock = Lock()

    def _wait_for(self, key: str, now: datetime) -> int:
        recent = [moment for moment in self._sent.get(key, []) if now - moment < HOUR]
        if recent:
            self._sent[key] = recent
        else:
            self._sent.pop(key, None)
        if len(recent) >= HOURLY_LIMIT:
            return int((recent[0] + HOUR - now).total_seconds()) + 1
        if recent and now - recent[-1] < COOLDOWN:
            return int((recent[-1] + COOLDOWN - now).total_seconds()) + 1
        return 0

    def start(self, user_id: int, phone: str, now: datetime) -> str:
        """A new code for this person and number; it replaces any earlier one."""
        keys = (f"user:{user_id}", f"phone:{phone}")
        with self._lock:
            wait = max(self._wait_for(key, now) for key in keys)
            if wait:
                raise TooManyCodesError(wait)
            for key in keys:
                self._sent.setdefault(key, []).append(now)
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._waiting[user_id] = (phone, code, now + LIFETIME, 0)
            return code

    def confirm(self, user_id: int, code: str, now: datetime) -> str | None:
        """The number this code proves, once. Too many wrong tries burn the code, so it cannot be guessed."""
        with self._lock:
            found = self._waiting.get(user_id)
            if found is None:
                return None
            phone, expected, expires_at, attempts = found
            if now >= expires_at:
                del self._waiting[user_id]
                return None
            if secrets.compare_digest(code, expected):
                del self._waiting[user_id]
                return phone
            if attempts + 1 >= MAX_ATTEMPTS:
                del self._waiting[user_id]
            else:
                self._waiting[user_id] = (phone, expected, expires_at, attempts + 1)
            return None
