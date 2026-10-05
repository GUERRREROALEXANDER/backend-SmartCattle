"""Shared API-key dependencies.

Two independent keys travel in the same `X-API-Key` header:

- `AI_API_KEY` authorizes writing events (service to service).
- `READ_API_KEY` authorizes reading events and animals.

A key that is not configured leaves its endpoints open, which is intended for
local development only. Keys are compared with `secrets.compare_digest`, whose
runtime does not depend on where the mismatch occurs, so response times cannot
be measured to guess a key character by character.
"""

import secrets

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from app.core.config import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def _authorized(provided: str | None, expected: SecretStr | None) -> bool:
    if expected is None:
        return True
    if provided is None:
        return False
    return secrets.compare_digest(
        provided.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    )


def verify_ingest_key(
    settings: Settings = Depends(get_app_settings),
    api_key: str | None = Security(api_key_header),
) -> None:
    if not _authorized(api_key, settings.ai_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def verify_read_key(
    settings: Settings = Depends(get_app_settings),
    api_key: str | None = Security(api_key_header),
) -> None:
    if not _authorized(api_key, settings.read_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
