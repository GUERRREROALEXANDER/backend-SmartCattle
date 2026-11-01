from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_catalog_store
from app.schemas.catalog import Catalogs
from app.services.catalog_store import CatalogStore

router = APIRouter(tags=["catalogs"])


@router.get("/api/catalogs", response_model=Catalogs)
def list_catalogs(store: Annotated[CatalogStore, Depends(get_catalog_store)]) -> Catalogs:
    """Every coded value the API uses, with English and Spanish labels.

    Public: the labels are not data about anyone, and the sign-up form needs
    the role names before there is a token.
    """
    return Catalogs(catalogs=store.all())
