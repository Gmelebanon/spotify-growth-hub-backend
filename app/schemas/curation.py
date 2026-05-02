from pydantic import BaseModel, field_validator, model_validator


class TrackIn(BaseModel):
    track_id: str
    track_name: str
    artist_name: str

    @field_validator("track_id", "track_name", "artist_name", mode="before")
    @classmethod
    def must_be_non_empty_string(cls, v: str, info) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()


class CurateRequest(BaseModel):
    source_playlist_tracks: list[TrackIn]
    my_tracks: list[TrackIn]
    ratio: list[int]

    @field_validator("ratio")
    @classmethod
    def validate_ratio(cls, v: list[int]) -> list[int]:
        if len(v) != 2:
            raise ValueError("ratio must contain exactly 2 integers")
        if v[0] <= 0 or v[1] <= 0:
            raise ValueError("both ratio values must be positive integers")
        return v


class TrackOut(BaseModel):
    track_id: str
    track_name: str
    artist_name: str


class CurateResponse(BaseModel):
    ordered_tracks: list[TrackOut]
    total_tracks: int
    source_tracks_used: int
    my_tracks_used: int
    skipped_duplicates: int
    skipped_artist_spacing: int