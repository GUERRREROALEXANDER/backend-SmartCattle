"""The PC at each farm: its key, and where it can be reached right now.

The AI service runs on a PC at the farm, because only something on that network can open its cameras. Each PC gets
its own key, valid only for the cameras of its account, so a far-away farm never holds a key to everyone's data.
The key is derived from JWT_SECRET instead of stored, so there is nothing new to keep in the database; replacing
that secret invalidates every key, along with every session.

Where a PC is reached is kept in memory: it reports every few seconds, so a restart forgets it only for a moment.
"""

import hashlib
import hmac
from datetime import datetime, timedelta
from threading import Lock

PREFIX = "farm-"
#: A PC reports every 30 seconds. Missing a few means it is off, or has no internet.
OFFLINE_AFTER = timedelta(seconds=90)


def _signature(account_id: int, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"smartcattle-farm-pc:{account_id}".encode(), hashlib.sha256).hexdigest()


def farm_key(account_id: int, secret: str) -> str:
    return f"{PREFIX}{account_id}-{_signature(account_id, secret)}"


def is_farm_key(key: str | None) -> bool:
    return key is not None and key.startswith(PREFIX)


def account_of(key: str, secret: str) -> int | None:
    """The account a farm key belongs to; None if it is not a genuine one."""
    account, _, signature = key.removeprefix(PREFIX).partition("-")
    if not account.isdecimal():
        return None
    account_id = int(account)
    return account_id if hmac.compare_digest(signature, _signature(account_id, secret)) else None


class FarmServices:
    """Where each account's PC said it can be reached."""

    def __init__(self) -> None:
        self._seen: dict[int, tuple[str, datetime]] = {}
        self._lock = Lock()

    def report(self, account_id: int, url: str, now: datetime) -> None:
        with self._lock:
            self._seen[account_id] = (url, now)

    def url_for(self, account_id: int | None, now: datetime) -> str | None:
        """Its address, unless it has gone quiet: a dead address would only make the panel wait."""
        if account_id is None:
            return None
        with self._lock:
            found = self._seen.get(account_id)
        return found[0] if found is not None and now - found[1] <= OFFLINE_AFTER else None
