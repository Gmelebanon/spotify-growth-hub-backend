import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from supabase import create_client, Client


router = APIRouter(prefix="/api/artist-library", tags=["artist-library"])


def get_supabase() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY",
        )

    return create_client(supabase_url, supabase_key)


class ArtistCreate(BaseModel):
    artist_id: str = Field(..., alias="artistId")
    name: str
    spotify_url: Optional[str] = Field(default=None, alias="spotifyUrl")
    image_url: Optional[str] = Field(default=None, alias="image")
    genres: List[str] = []
    streams: int = 0
    growth_percent: float = Field(default=0, alias="growthPercent")

    class Config:
        populate_by_name = True


class ArtistFollowerSnapshotIn(BaseModel):
    artist_id: str = Field(..., alias="artistId")
    followers: int

    class Config:
        populate_by_name = True


class SyncFollowersRequest(BaseModel):
    artists: List[ArtistFollowerSnapshotIn]


def calculate_7_day_followers(
    supabase: Client,
    artist_id: str,
    current_followers: int,
) -> int:
    seven_days_ago = date.today() - timedelta(days=7)

    response = (
        supabase.table("artist_follower_snapshots")
        .select("followers")
        .eq("artist_id", artist_id)
        .lte("snapshot_date", seven_days_ago.isoformat())
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return 0

    previous_followers = int(rows[0].get("followers") or 0)

    return int(current_followers) - previous_followers


@router.get("")
def get_artist_library() -> Dict[str, Any]:
    supabase = get_supabase()

    response = (
        supabase.table("artist_library")
        .select("*")
        .eq("is_active", True)
        .order("created_at", desc=False)
        .execute()
    )

    artists = response.data or []

    enriched_artists = []

    for artist in artists:
        artist_id = artist.get("artist_id")
        current_snapshot_response = (
            supabase.table("artist_follower_snapshots")
            .select("followers")
            .eq("artist_id", artist_id)
            .eq("snapshot_date", date.today().isoformat())
            .limit(1)
            .execute()
        )

        current_snapshot_rows = current_snapshot_response.data or []
        current_followers = (
            int(current_snapshot_rows[0].get("followers") or 0)
            if current_snapshot_rows
            else 0
        )

        followers_7_days = calculate_7_day_followers(
            supabase=supabase,
            artist_id=artist_id,
            current_followers=current_followers,
        )

        enriched_artists.append(
            {
                "id": artist_id,
                "artistId": artist_id,
                "name": artist.get("name"),
                "spotifyUrl": artist.get("spotify_url"),
                "image": artist.get("image_url"),
                "genres": artist.get("genres") or [],
                "streams": artist.get("streams") or 0,
                "growthPercent": artist.get("growth_percent") or 0,
                "followers7Days": followers_7_days,
                "isActive": artist.get("is_active"),
                "createdAt": artist.get("created_at"),
                "updatedAt": artist.get("updated_at"),
            }
        )

    return {
        "success": True,
        "artists": enriched_artists,
    }


@router.post("")
def add_artist(payload: ArtistCreate) -> Dict[str, Any]:
    supabase = get_supabase()

    artist_payload = {
        "artist_id": payload.artist_id,
        "name": payload.name,
        "spotify_url": payload.spotify_url,
        "image_url": payload.image_url,
        "genres": payload.genres,
        "streams": payload.streams,
        "growth_percent": payload.growth_percent,
        "is_active": True,
        "updated_at": "now()",
    }

    response = (
        supabase.table("artist_library")
        .upsert(artist_payload, on_conflict="artist_id")
        .execute()
    )

    return {
        "success": True,
        "artist": response.data[0] if response.data else artist_payload,
    }


@router.delete("/{artist_id}")
def remove_artist(artist_id: str) -> Dict[str, Any]:
    supabase = get_supabase()

    response = (
        supabase.table("artist_library")
        .update(
            {
                "is_active": False,
                "updated_at": "now()",
            }
        )
        .eq("artist_id", artist_id)
        .execute()
    )

    return {
        "success": True,
        "artistId": artist_id,
        "result": response.data,
    }


@router.post("/sync-followers")
def sync_followers(payload: SyncFollowersRequest) -> Dict[str, Any]:
    supabase = get_supabase()

    today = date.today().isoformat()
    synced = []

    for artist in payload.artists:
        row = {
            "artist_id": artist.artist_id,
            "followers": artist.followers,
            "snapshot_date": today,
        }

        supabase.table("artist_follower_snapshots").upsert(
            row,
            on_conflict="artist_id,snapshot_date",
        ).execute()

        followers_7_days = calculate_7_day_followers(
            supabase=supabase,
            artist_id=artist.artist_id,
            current_followers=artist.followers,
        )

        synced.append(
            {
                "artistId": artist.artist_id,
                "followers": artist.followers,
                "followers7Days": followers_7_days,
            }
        )

    return {
        "success": True,
        "snapshotDate": today,
        "artists": synced,
    }