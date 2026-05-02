from datetime import datetime

from pydantic import BaseModel, field_validator


class ReleaseCreate(BaseModel):
    upc: str
    artist: str
    status: str

    @field_validator("upc", "artist", "status", mode="before")
    @classmethod
    def must_be_non_empty(cls, v: str, info) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()


class ReleaseOut(BaseModel):
    id: int
    upc: str
    artist: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}