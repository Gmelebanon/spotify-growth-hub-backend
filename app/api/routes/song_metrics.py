from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db


router = APIRouter(prefix="/api/song-metrics", tags=["song-metrics"])


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


def db_row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


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


SONG_METRIC_COLUMNS = """
    row_id,
    track_id,
    artist_id,
    artist_name,
    song_name,
    release_date,
    days,
    streams,
    listeners,
    saves,
    save_rate,
    radio_rate,
    playlists,
    completion_rate,
    status,
    master_group,
    spotify_url,
    release_name,
    image_url,
    duration,
    album_id,
    source,
    created_at,
    updated_at
"""


UPSERT_SONG_METRIC_SQL = text(
    """
    INSERT INTO song_metrics (
        row_id,
        track_id,
        artist_id,
        artist_name,
        song_name,
        release_date,
        days,
        streams,
        listeners,
        saves,
        save_rate,
        radio_rate,
        playlists,
        completion_rate,
        status,
        master_group,
        spotify_url,
        release_name,
        image_url,
        duration,
        album_id,
        source,
        updated_at
    ) VALUES (
        :row_id,
        :track_id,
        :artist_id,
        :artist_name,
        :song_name,
        CAST(:release_date AS date),
        :days,
        :streams,
        :listeners,
        :saves,
        :save_rate,
        :radio_rate,
        :playlists,
        :completion_rate,
        :status,
        :master_group,
        :spotify_url,
        :release_name,
        :image_url,
        :duration,
        :album_id,
        :source,
        CAST(:updated_at AS timestamp)
    )
    ON CONFLICT (row_id) DO UPDATE SET
        track_id = EXCLUDED.track_id,
        artist_id = EXCLUDED.artist_id,
        artist_name = EXCLUDED.artist_name,
        song_name = EXCLUDED.song_name,
        release_date = EXCLUDED.release_date,
        days = EXCLUDED.days,
        streams = EXCLUDED.streams,
        listeners = EXCLUDED.listeners,
        saves = EXCLUDED.saves,
        save_rate = EXCLUDED.save_rate,
        radio_rate = EXCLUDED.radio_rate,
        playlists = EXCLUDED.playlists,
        completion_rate = EXCLUDED.completion_rate,
        status = EXCLUDED.status,
        master_group = EXCLUDED.master_group,
        spotify_url = EXCLUDED.spotify_url,
        release_name = EXCLUDED.release_name,
        image_url = EXCLUDED.image_url,
        duration = EXCLUDED.duration,
        album_id = EXCLUDED.album_id,
        source = EXCLUDED.source,
        updated_at = EXCLUDED.updated_at
    RETURNING *
    """
)


@router.get("")
def get_song_metrics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        result = db.execute(
            text(
                f"""
                SELECT {SONG_METRIC_COLUMNS}
                FROM song_metrics
                ORDER BY release_date DESC NULLS LAST, created_at DESC NULLS LAST
                """
            )
        )
        rows = [db_row_to_dict(row) for row in result.fetchall()]

        return {
            "success": True,
            "source": "DATABASE_URL",
            "rows": [row_from_database(row) for row in rows],
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Song metrics database error: {str(error)}",
        )


@router.post("/bulk-upsert")
def bulk_upsert_song_metrics(
    payload: BulkSongMetricsRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        if not payload.rows:
            return {"success": True, "source": "DATABASE_URL", "saved": 0, "rows": []}

        rows = [row_to_database(row) for row in payload.rows]
        saved_rows: List[Dict[str, Any]] = []

        for row in rows:
            result = db.execute(UPSERT_SONG_METRIC_SQL, row)
            saved_row = result.fetchone()
            if saved_row is not None:
                saved_rows.append(db_row_to_dict(saved_row))

        db.commit()

        return {
            "success": True,
            "source": "DATABASE_URL",
            "saved": len(saved_rows),
            "rows": [row_from_database(row) for row in saved_rows],
        }

    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Could not save song metrics: {str(error)}",
        )


@router.delete("/{row_id}")
def delete_song_metric(row_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        result = db.execute(
            text(
                """
                DELETE FROM song_metrics
                WHERE row_id = :row_id
                RETURNING row_id
                """
            ),
            {"row_id": row_id},
        )
        deleted = [db_row_to_dict(row) for row in result.fetchall()]
        db.commit()

        return {
            "success": True,
            "source": "DATABASE_URL",
            "id": row_id,
            "deleted": len(deleted),
            "result": deleted,
        }

    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete song metric: {str(error)}",
        )
