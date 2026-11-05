from fastapi import APIRouter, Depends

from app.core.security import media_jwks
from app.dependencies import require_jwt_secret

router = APIRouter(tags=["cameras"])


@router.get("/api/media/jwks.json")
def media_public_keys(secret: str = Depends(require_jwt_secret)) -> dict[str, list[dict[str, str]]]:
    """The public key media servers verify video tickets with. Public by nature, and it has no session to send."""
    return media_jwks(secret)
