from datetime import datetime

from pydantic import BaseModel, field_validator


class SyncGroupCreate(BaseModel):
    name: str
    master_playlist_id: int
    child_playlist_ids: list[int]

    @field_validator("name", mode="before")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("name must be a non-empty string")
        return v.strip()

    @field_validator("master_playlist_id", mode="before")
    @classmethod
    def master_must_be_positive(cls, v: int) -> int:
        if not isinstance(v, int) or v <= 0:
            raise ValueError("master_playlist_id must be a positive integer")
        return v

    @field_validator("child_playlist_ids", mode="before")
    @classmethod
    def children_must_be_valid(cls, v: list) -> list[int]:
        if not isinstance(v, list):
            raise ValueError("child_playlist_ids must be a list of integers")
        for item in v:
            if not isinstance(item, int) or item <= 0:
                raise ValueError(
                    f"each child_playlist_id must be a positive integer, got: {item}"
                )
        return v


class SyncGroupOut(BaseModel):
    id: int
    name: str
    master_playlist_id: int
    child_playlist_ids: list[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class SyncExecuteRequest(BaseModel):
    group_id: int

    @field_validator("group_id", mode="before")
    @classmethod
    def group_id_must_be_positive(cls, v: int) -> int:
        if not isinstance(v, int) or v <= 0:
            raise ValueError("group_id must be a positive integer")
        return v


class SyncExecuteResponse(BaseModel):
    group_id: int
    master_playlist_id: int
    number_of_child_playlists: int
    tracks_copied: int
    execution_log: list[str]