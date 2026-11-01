from pydantic import BaseModel, Field


class CatalogEntry(BaseModel):
    """One allowed value of a coded column, with what to show for it."""

    code: str = Field(description="The value stored in the coded column")
    label: str = Field(description="English label")
    label_es: str = Field(description="Spanish label, for the farm staff")
    description: str
    sort_order: int


class Catalogs(BaseModel):
    """Every catalog, keyed by name. The frontend builds its selects and badges from this."""

    model_config = {"json_schema_extra": {"examples": [{"catalogs": {"lot_kinds": [{
        "code": "paddock", "label": "Paddock", "label_es": "Potrero",
        "description": "Fenced grazing area", "sort_order": 1,
    }]}}]}}

    catalogs: dict[str, list[CatalogEntry]]
