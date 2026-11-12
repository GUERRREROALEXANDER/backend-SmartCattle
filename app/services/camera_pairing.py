"""Short-lived codes behind the QR on the Cameras page.

The QR opens a page on the phone, which has no session. The code stands in for
one, for one thing only: saving one camera in the account that asked for it.
Single use and short-lived, because a QR can be photographed. Kept in memory, like
the live channel: a restart only means showing the QR again.
"""

import secrets
from datetime import datetime, timedelta
from threading import Lock

LIFETIME = timedelta(minutes=10)
#: A code is only ever waiting a few minutes; this bounds what a loop can make the server hold.
MAX_WAITING = 500


#: No letters that read like digits: this code gets typed by hand on the farm's PC.
SHORT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class CameraPairings:
    def __init__(self) -> None:
        self._codes: dict[str, tuple[int, datetime]] = {}
        self._lock = Lock()

    def issue_short(self, account_id: int, now: datetime) -> str:
        return self.issue(account_id, now, "".join(secrets.choice(SHORT_ALPHABET) for _ in range(8)))

    def issue(self, account_id: int, now: datetime, code: str | None = None) -> str:
        code = code or secrets.token_urlsafe(24)
        with self._lock:
            self._codes = {key: item for key, item in self._codes.items() if item[1] > now}
            if len(self._codes) >= MAX_WAITING:
                self._codes.pop(next(iter(self._codes)))
            self._codes[code] = (account_id, now + LIFETIME)
        return code

    def redeem(self, code: str, now: datetime) -> int | None:
        """The account behind a code, once. None if it is unknown, used or expired."""
        with self._lock:
            found = self._codes.pop(code, None)
        return found[0] if found is not None and found[1] > now else None
