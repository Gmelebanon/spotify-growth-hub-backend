from pydantic import BaseModel
from datetime import datetime


class DashboardSummaryOut(BaseModel):
    total_playlists: int
    total_followers: int
    total_growth_last_24h: int


class TopPlaylistItem(BaseModel):
    playlist_id: int
    name: str
    followers: int
    growth: int
    created_at: datetime

    model_config = {"from_attributes": True}


class TopPlaylistsOut(BaseModel):
    limit: int
    results: list[TopPlaylistItem]