from datetime import date

from pydantic import BaseModel


class FollowerHistoryOut(BaseModel):
    id: int
    playlist_id: int
    date: date
    followers: int

    model_config = {"from_attributes": True}