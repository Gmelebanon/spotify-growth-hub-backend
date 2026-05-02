from pydantic import BaseModel, field_validator


class GenerateTitleRequest(BaseModel):
    keyword: str

    @field_validator("keyword", mode="before")
    @classmethod
    def keyword_must_be_non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("keyword must be a non-empty string")
        return v.strip()


class GenerateTitleResponse(BaseModel):
    title: str


class TagTrackRequest(BaseModel):
    track_name: str
    artist_name: str

    @field_validator("track_name", "artist_name", mode="before")
    @classmethod
    def must_be_non_empty(cls, v: str, info) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()


class TagTrackResponse(BaseModel):
    bpm_bucket: str
    energy_level: str
    genre: str