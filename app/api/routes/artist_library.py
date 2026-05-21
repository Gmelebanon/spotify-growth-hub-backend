import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from supabase import Client, create_client


router = APIRouter(prefix="/api/artist-library", tags=["artist-library"])


def get_supabase() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_URL")

    if not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Missing SUPABASE_SERVICE_ROLE_KEY",
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
    followers: int = 0
    popularity: int = 0
    total_releases: int = Field(default=0, alias="totalReleases")
    total_tracks: int = Field(default=0, alias="totalTracks")
    latest_release: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="latestRelease",
    )

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
    try:
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
            current_followers = int(artist.get("followers") or 0)

            snapshot_response = (
                supabase.table("artist_follower_snapshots")
                .select("followers")
                .eq("artist_id", artist_id)
                .eq("snapshot_date", date.today().isoformat())
                .limit(1)
                .execute()
            )

            snapshot_rows = snapshot_response.data or []

            if snapshot_rows:
                current_followers = int(
                    snapshot_rows[0].get("followers") or current_followers
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
                    "followers": current_followers,
                    "followers7Days": followers_7_days,
                    "popularity": artist.get("popularity") or 0,
                    "totalReleases": artist.get("total_releases") or 0,
                    "totalTracks": artist.get("total_tracks") or 0,
                    "latestRelease": artist.get("latest_release"),
                    "isActive": artist.get("is_active"),
                    "createdAt": artist.get("created_at"),
                    "updatedAt": artist.get("updated_at"),
                }
            )

        return {
            "success": True,
            "artists": enriched_artists,
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Artist library database error: {str(error)}",
        )


@router.post("")
def add_artist(payload: ArtistCreate) -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        now = datetime.utcnow().isoformat()

        artist_payload = {
            "artist_id": payload.artist_id,
            "name": payload.name,
            "spotify_url": payload.spotify_url,
            "image_url": payload.image_url,
            "genres": payload.genres,
            "streams": payload.streams,
            "growth_percent": payload.growth_percent,
            "followers": payload.followers,
            "popularity": payload.popularity,
            "total_releases": payload.total_releases,
            "total_tracks": payload.total_tracks,
            "latest_release": payload.latest_release,
            "is_active": True,
            "updated_at": now,
        }

        response = (
            supabase.table("artist_library")
            .upsert(artist_payload, on_conflict="artist_id")
            .execute()
        )

        snapshot_payload = {
            "artist_id": payload.artist_id,
            "followers": payload.followers,
            "snapshot_date": date.today().isoformat(),
        }

        supabase.table("artist_follower_snapshots").upsert(
            snapshot_payload,
            on_conflict="artist_id,snapshot_date",
        ).execute()

        return {
            "success": True,
            "artist": response.data[0] if response.data else artist_payload,
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not add artist: {str(error)}",
        )


@router.delete("/{artist_id}")
def remove_artist(artist_id: str) -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        now = datetime.utcnow().isoformat()

        response = (
            supabase.table("artist_library")
            .update(
                {
                    "is_active": False,
                    "updated_at": now,
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

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not remove artist: {str(error)}",
        )


@router.post("/sync-followers")
def sync_followers(payload: SyncFollowersRequest) -> Dict[str, Any]:
    try:
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

            supabase.table("artist_library").update(
                {
                    "followers": artist.followers,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            ).eq("artist_id", artist.artist_id).execute()

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

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not sync followers: {str(error)}",
        )