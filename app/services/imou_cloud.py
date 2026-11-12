"""The Imou cloud: which Imou cameras the platform's developer account can show from anywhere.

A camera's LAN address (192.168.x.x) only exists inside the farm's Wi-Fi, so a server on the internet can never open
it. An Imou camera also answers through Imou's own cloud by its serial number, wherever the server is. This client
lists the cameras of the developer account (open.imoulife.com) so the dashboard can recommend them; the AI service
gets the live video of each one through the same cloud.

The Imou cloud does not return a camera's LAN address: the address shown in the dashboard is the one the person typed.
"""

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

DEFAULT_API_URL = "https://openapi.easy4ip.com/openapi"
#: The device list changes when someone adds a camera in the Imou app; half a minute is fresh enough.
CACHE_SECONDS = 30.0
PAGE_SIZE = 50
#: A developer account with more cameras than this is not a farm.
MAX_PAGES = 10

Post = Callable[[str, dict[str, Any]], dict[str, Any]]


class ImouError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ImouDevice(BaseModel):
    serial: str
    name: str
    model: str | None = None
    online: bool


def _http_post(url: str, body: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    request = Request(  # noqa: S310 - fixed https base URL from settings
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https base URL from settings
            answer = json.load(response)
            return answer if isinstance(answer, dict) else {}
    except HTTPError as error:
        raise ImouError(f"Imou answered HTTP {error.code}") from error
    except (URLError, OSError) as error:
        raise ImouError("Could not reach the Imou cloud") from error
    except ValueError as error:
        raise ImouError("The Imou cloud sent an invalid answer") from error


def _device(item: dict[str, Any]) -> ImouDevice | None:
    serial = str(item.get("deviceId") or "").strip()
    if not serial:
        return None
    status = str(item.get("deviceStatus") or item.get("status") or "").strip().lower()
    name = str(item.get("deviceName") or item.get("name") or "").strip() or f"Imou {serial}"
    model = str(item.get("deviceModel") or "").strip() or None
    return ImouDevice(serial=serial, name=name[:80], model=model, online=status in {"online", "1"})


class ImouCloud:
    """Signed calls to the Imou Open Platform HTTP API, with the device list cached for a moment."""

    def __init__(self, app_id: str, app_secret: str, base_url: str = DEFAULT_API_URL, *,
                 post: Post | None = None, clock: Callable[[], float] = time.time) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._post = post or _http_post
        self._clock = clock
        self._lock = Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._devices: list[ImouDevice] | None = None
        self._devices_at = 0.0

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        now = round(self._clock())
        nonce = secrets.token_hex(16)
        sign = hashlib.md5(  # noqa: S324 - the signature scheme Imou defines, not a password hash
            f"time:{now},nonce:{nonce},appSecret:{self._app_secret}".encode(),
        ).hexdigest()
        body = {
            "system": {"ver": "1.0", "sign": sign, "appId": self._app_id, "time": now, "nonce": nonce},
            "params": params,
            "id": secrets.token_hex(8),
        }
        answer = self._post(f"{self._base_url}/{method}", body)
        result = answer.get("result") if isinstance(answer, dict) else None
        if not isinstance(result, dict):
            raise ImouError("The Imou cloud sent an invalid answer")
        code = str(result.get("code", ""))
        if code != "0":
            # The message can name the app id; the code alone is enough to look it up.
            raise ImouError(f"The Imou cloud refused the request ({code or 'no code'})", code or None)
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    def _with_token(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            if self._token is None or self._clock() >= self._token_expires_at:
                data = self._call("accessToken", {})
                token = data.get("accessToken")
                if not isinstance(token, str) or not token:
                    raise ImouError("The Imou cloud gave no access token")
                self._token = token
                lifetime = float(data.get("expireTime") or 0)
                self._token_expires_at = self._clock() + max(lifetime - 60, 60)
            try:
                return self._call(method, {**params, "token": self._token})
            except ImouError as error:
                if error.code != "TK1002" or attempt:
                    raise
                self._token = None  # Expired or revoked: get a new one and try once more.
        raise AssertionError("unreachable")

    def _fetch_devices(self) -> list[ImouDevice]:
        devices: dict[str, ImouDevice] = {}
        for page in range(1, MAX_PAGES + 1):
            data = self._with_token("listDeviceDetailsByPage",
                                    {"page": page, "pageSize": PAGE_SIZE, "source": "bindAndShare"})
            items = data.get("deviceList")
            items = items if isinstance(items, list) else []
            for item in items:
                device = _device(item) if isinstance(item, dict) else None
                if device is not None:
                    devices.setdefault(device.serial, device)
            if len(items) < PAGE_SIZE:
                break
        return sorted(devices.values(), key=lambda device: (not device.online, device.name.lower()))

    def devices(self) -> list[ImouDevice]:
        """The cameras of the developer account, online ones first."""
        with self._lock:
            if self._devices is None or self._clock() - self._devices_at > CACHE_SECONDS:
                self._devices = self._fetch_devices()
                self._devices_at = self._clock()
            return list(self._devices)

    def device(self, serial: str) -> ImouDevice | None:
        return next((device for device in self.devices() if device.serial == serial), None)
