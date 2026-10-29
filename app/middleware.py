"""Request correlation.

A 500 in the Render log and a failed request in the browser had nothing in
common to join them on. This gives every request an id, puts it in the
response and in the log line, so a user reporting "it failed at 14:32" can be
matched to the traceback that caused it.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

#: Returned on every response, and accepted on the way in so a caller that
#: already has one -- a frontend retrying, a proxy, a future AI service -- keeps
#: the same id across the hop instead of the chain breaking at our door.
REQUEST_ID_HEADER = "X-Request-ID"

#: An inbound id is used only if it looks like one. Echoing arbitrary
#: caller-supplied text into every log line lets whoever sends it forge log
#: entries, and a 4 KB header would bloat every line it appears in.
MAX_REQUEST_ID_LENGTH = 64


def _safe_request_id(supplied: str | None) -> str:
    if supplied is None:
        return uuid.uuid4().hex
    candidate = supplied.strip()
    if not candidate or len(candidate) > MAX_REQUEST_ID_LENGTH:
        return uuid.uuid4().hex
    # Conservative on purpose: anything that could end a log line or be read
    # as structure is replaced rather than escaped.
    if not all(character.isalnum() or character in "-_" for character in candidate):
        return uuid.uuid4().hex
    return candidate


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tags each request with an id and logs how it finished."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _safe_request_id(request.headers.get(REQUEST_ID_HEADER))
        # Stored on the request so a handler or an exception handler can read
        # it without the header being parsed again.
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Logged here as well as by the application's exception handler,
            # because a failure inside another middleware never reaches that
            # handler and would otherwise vanish.
            logger.exception(
                "request failed", extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "%s %s -> %s in %.1f ms",
            request.method, request.url.path, response.status_code, duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
        return response
