from datetime import date, datetime

from pydantic import BaseModel, field_validator


class TradeCreate(BaseModel):
    track_name: str
    playlist: str
    start_date: date

    @field_validator("track_name", "playlist", mode="before")
    @classmethod
    def must_be_non_empty(cls, v: str, info) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()


class TradeOut(BaseModel):
    id: int
    track_name: str
    playlist: str
    start_date: date
    expiry_date: date
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}