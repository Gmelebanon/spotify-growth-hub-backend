import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from supabase import Client, create_client


router = APIRouter(prefix="/api/song-metrics", tags=["song-metrics"])


def get_supabase() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_URL")

    if not supabase_key:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_SERVICE_ROLE_KEY")

    return create_client(supabase_url, supabase_key)


class SongMetricIn(BaseModel):
    id: str
    track_id: Optional[str] = Field(default=None, alias="trackId")
    song: str
    artist: str
    artist_id: Optional[str] = Field(default=None, alias="artistId")
    released: Optional[str] = None
    days: int = 0
    streams: Optional[int] = None
    listeners: Optional[int] = None
    saves: Optional[int] = None
    save_rate: Optional[float] = Field(default=None, alias="saveRate")
    radio_rate: Optional[float] = Field(default=None, alias="radioRate")
    playlists: Optional[int] = None
    completion_rate: Optional[float] = Field(default=None, alias="completionRate")
    status: str = "Review"
    master_group: Optional[str] = Field(default=None, alias="masterGroup")
    spotify_url: Optional[str] = Field(default=None, alias="spotifyUrl")
    release_name: Optional[str] = Field(default=None, alias="releaseName")
    image_url: Optional[str] = Field(default=None, alias="imageUrl")
    duration: Optional[str] = None
    album_id: Optional[str] = Field(default=None, alias="albumId")
    source: str = "spotify"

    class Config:
        populate_by_name = True


class BulkSongMetricsRequest(BaseModel):
    rows: List[SongMetricIn]


def normalize_status(status: Optional[str]) -> str:
    if status in ["Keep", "Flagged", "Review"]:
        return status
    return "Review"


def normalize_release_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    try:
        parsed = date.fromisoformat(value[:10])
        return parsed.isoformat()
    except Exception:
        return None


def row_to_database(row: SongMetricIn) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()

    return {
        "row_id": row.id,
        "track_id": row.track_id,
        "artist_id": row.artist_id,
        "artist_name": row.artist,
        "song_name": row.song,
        "release_date": normalize_release_date(row.released),
        "days": row.days or 0,
        "streams": row.streams,
        "listeners": row.listeners,
        "saves": row.saves,
        "save_rate": row.save_rate,
        "radio_rate": row.radio_rate,
        "playlists": row.playlists,
        "completion_rate": row.completion_rate,
        "status": normalize_status(row.status),
        "master_group": row.master_group or row.song,
        "spotify_url": row.spotify_url,
        "release_name": row.release_name,
        "image_url": row.image_url,
        "duration": row.duration,
        "album_id": row.album_id,
        "source": row.source or "spotify",
        "updated_at": now,
    }


def row_from_database(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("row_id"),
        "trackId": row.get("track_id"),
        "song": row.get("song_name"),
        "artist": row.get("artist_name"),
        "artistId": row.get("artist_id"),
        "released": row.get("release_date") or "",
        "days": row.get("days") or 0,
        "streams": row.get("streams"),
        "listeners": row.get("listeners"),
        "saves": row.get("saves"),
        "saveRate": row.get("save_rate"),
        "radioRate": row.get("radio_rate"),
        "playlists": row.get("playlists"),
        "completionRate": row.get("completion_rate"),
        "status": row.get("status") or "Review",
        "masterGroup": row.get("master_group") or row.get("song_name"),
        "spotifyUrl": row.get("spotify_url"),
        "releaseName": row.get("release_name"),
        "imageUrl": row.get("image_url"),
        "duration": row.get("duration"),
        "albumId": row.get("album_id"),
        "source": row.get("source") or "database",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@router.get("")
def get_song_metrics() -> Dict[str, Any]:
    try:
        supabase = get_supabase()

        response = (
            supabase.table("song_metrics")
            .select("*")
            .order("release_date", desc=True)
            .execute()
        )

        rows = response.data or []

        return {
            "success": True,
            "rows": [row_from_database(row) for row in rows],
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Song metrics database error: {str(error)}",
        )


@router.post("/bulk-upsert")
def bulk_upsert_song_metrics(payload: BulkSongMetricsRequest) -> Dict[str, Any]:
    try:
        supabase = get_supabase()

        if not payload.rows:
            return {"success": True, "saved": 0, "rows": []}

        rows = [row_to_database(row) for row in payload.rows]

        response = (
            supabase.table("song_metrics")
            .upsert(rows, on_conflict="row_id")
            .execute()
        )

        saved_rows = response.data or rows

        return {
            "success": True,
            "saved": len(saved_rows),
            "rows": [row_from_database(row) for row in saved_rows],
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save song metrics: {str(error)}",
        )


@router.delete("/{row_id}")
def delete_song_metric(row_id: str) -> Dict[str, Any]:
    try:
        supabase = get_supabase()

        response = (
            supabase.table("song_metrics")
            .delete()
            .eq("row_id", row_id)
            .execute()
        )

        return {
            "success": True,
            "id": row_id,
            "result": response.data,
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete song metric: {str(error)}",
        )