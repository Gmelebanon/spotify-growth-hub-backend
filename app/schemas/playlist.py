from datetime import datetime
from pydantic import BaseModel
from app.schemas.follower_history import FollowerHistoryOut


# Playlist list item now includes growth
class PlaylistOut(BaseModel):
    id: int
    name: str
    followers: int
    created_at: datetime
    growth: int = 0

    model_config = {"from_attributes": True}


class PlaylistGrowthOut(BaseModel):
    playlist_id: int
    current_followers: int
    growth: int
    history: list[FollowerHistoryOut]