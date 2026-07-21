import base64
import json
import os
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db


router = APIRouter(prefix="/api/artist-library", tags=["artist-library"])

GROWTH_LOGIC_VERSION = "database-url-batched-v1"


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
    latest_release: Optional[Dict[str, Any]] = Field(default=None, alias="latestRelease")
    recent_releases: List[Dict[str, Any]] = Field(default=[], alias="recentReleases")

    class Config:
        populate_by_name = True


class ArtistFollowerSnapshotIn(BaseModel):
    artist_id: str = Field(..., alias="artistId")
    followers: int

    class Config:
        populate_by_name = True


class SyncFollowersRequest(BaseModel):
    artists: List[ArtistFollowerSnapshotIn]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


def as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value if value is not None else fallback)
    except Exception:
        return fallback


def json_param(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False)


def normalize_json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return value


def get_best_image(images: Any) -> Optional[str]:
    if not isinstance(images, list) or len(images) == 0:
        return None
    first_image = images[0] or {}
    return first_image.get("url")


def format_duration(ms: int) -> str:
    total_seconds = int(ms or 0) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def get_release_days_ago(release_date: Optional[str]) -> Optional[int]:
    if not release_date:
        return None
    try:
        release_day = date.fromisoformat(release_date[:10])
    except ValueError:
        return None
    return (date.today() - release_day).days



def normalize_match_value(value: Any) -> str:
    import unicodedata
    import re

    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = normalized.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def parse_scheduling_release_date(value: Any) -> Optional[date]:
    raw = str(value or "").strip()
    if not raw:
        return None

    for date_format in (
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%m-%d-%Y",
        "%Y.%m.%d",
        "%m.%d.%Y",
    ):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue

    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def build_spotify_release_references(metadata: Dict[str, Any]) -> Dict[str, set[str]]:
    album_names: set[str] = set()
    song_names: set[str] = set()

    releases: List[Dict[str, Any]] = []
    latest_release = metadata.get("latest_release")
    if isinstance(latest_release, dict):
        releases.append(latest_release)

    recent_releases = metadata.get("recent_releases") or []
    if isinstance(recent_releases, list):
        releases.extend(
            release for release in recent_releases if isinstance(release, dict)
        )

    seen_release_ids: set[str] = set()
    for release in releases:
        release_id = str(release.get("id") or "")
        if release_id and release_id in seen_release_ids:
            continue
        if release_id:
            seen_release_ids.add(release_id)

        album_name = normalize_match_value(release.get("name"))
        if album_name:
            album_names.add(album_name)

        tracks = release.get("tracks") or []
        if isinstance(tracks, list):
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                song_name = normalize_match_value(track.get("name"))
                if song_name:
                    song_names.add(song_name)

    return {"albums": album_names, "songs": song_names}


def push_matching_scheduling_rows_online(
    db: Session,
    artist_name: str,
    metadata: Dict[str, Any],
) -> List[int]:
    normalized_artist = normalize_match_value(artist_name)
    if not normalized_artist:
        return []

    references = build_spotify_release_references(metadata)
    album_names = references["albums"]
    song_names = references["songs"]

    if not album_names and not song_names:
        return []

    result = db.execute(
        text(
            """
            SELECT id, artist, album, song, release_date, status, platform_status
            FROM public.scheduling_rows
            WHERE COALESCE(TRIM(artist), '') <> ''
            """
        )
    )

    today = date.today()
    matching_ids: List[int] = []

    for row in result.fetchall():
        scheduling_row = to_dict(row)
        stored_status = str(scheduling_row.get("status") or "").strip()

        if stored_status in {"Online", "Rejected", "No Artist"}:
            continue

        release_day = parse_scheduling_release_date(scheduling_row.get("release_date"))
        if release_day is None or release_day > today:
            continue

        row_artist = normalize_match_value(scheduling_row.get("artist"))
        row_album = normalize_match_value(scheduling_row.get("album"))
        row_song = normalize_match_value(scheduling_row.get("song"))

        if row_artist != normalized_artist:
            continue

        album_match = bool(row_album and row_album in album_names)
        song_match = bool(row_song and row_song in song_names)

        if album_match or song_match:
            matching_ids.append(int(scheduling_row["id"]))

    if not matching_ids:
        return []

    updated = db.execute(
        text(
            """
            UPDATE public.scheduling_rows
            SET status = 'Online',
                platform_status = 'online'
            WHERE id = ANY(:row_ids)
            RETURNING id
            """
        ),
        {"row_ids": matching_ids},
    ).fetchall()

    return [int(to_dict(row)["id"]) for row in updated]


def get_spotify_access_token() -> str:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET",
        )

    basic_token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    with httpx.Client(timeout=30) as client:
        response = client.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {basic_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Spotify token request failed: {response.text}",
        )

    return response.json()["access_token"]


def spotify_get(url: str, access_token: str) -> Dict[str, Any]:
    with httpx.Client(timeout=45) as client:
        response = client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Spotify request failed: {response.text}",
        )

    return response.json()


def spotify_get_all_items(url: str, access_token: str) -> List[Dict[str, Any]]:
    """Follow Spotify pagination until every item has been collected."""
    items: List[Dict[str, Any]] = []
    next_url: Optional[str] = url

    while next_url:
        page = spotify_get(next_url, access_token)
        page_items = page.get("items") or []
        if isinstance(page_items, list):
            items.extend(item for item in page_items if isinstance(item, dict))

        raw_next = page.get("next")
        next_url = raw_next if isinstance(raw_next, str) and raw_next else None

    return items


def get_album_tracks(album_id: str, access_token: str) -> List[Dict[str, Any]]:
    tracks_data = spotify_get(
        f"https://api.spotify.com/v1/albums/{album_id}/tracks?market=US&limit=50",
        access_token,
    )

    tracks = []
    for track in tracks_data.get("items") or []:
        track_id = track.get("id")
        tracks.append(
            {
                "id": track_id,
                "name": track.get("name"),
                "trackNumber": track.get("track_number"),
                "duration": format_duration(track.get("duration_ms") or 0),
                "explicit": bool(track.get("explicit")),
                "artists": [
                    artist.get("name")
                    for artist in (track.get("artists") or [])
                    if artist.get("name")
                ],
                "spotifyUrl": (
                    (track.get("external_urls") or {}).get("spotify")
                    or f"https://open.spotify.com/track/{track_id}"
                ),
            }
        )
    return tracks


def normalize_release(
    release: Dict[str, Any],
    access_token: str,
    include_tracks: bool = True,
) -> Dict[str, Any]:
    release_id = release.get("id")
    tracks = get_album_tracks(release_id, access_token) if include_tracks and release_id else []

    return {
        "id": release_id,
        "name": release.get("name"),
        "type": release.get("album_type"),
        "releaseDate": release.get("release_date"),
        "totalTracks": release.get("total_tracks") or len(tracks) or 1,
        "image": get_best_image(release.get("images")),
        "spotifyUrl": (
            (release.get("external_urls") or {}).get("spotify")
            or f"https://open.spotify.com/album/{release_id}"
        ),
        "tracks": tracks,
    }


def fetch_spotify_artist_metadata(artist_id: str, access_token: str) -> Dict[str, Any]:
    artist_data = spotify_get(
        f"https://api.spotify.com/v1/artists/{artist_id}",
        access_token,
    )

    album_items = spotify_get_all_items(
        f"https://api.spotify.com/v1/artists/{artist_id}/albums"
        "?include_groups=album,single&market=US&limit=50",
        access_token,
    )

    unique_releases: Dict[str, Dict[str, Any]] = {}
    for release in album_items:
        release_id = release.get("id")
        if release_id and release_id not in unique_releases:
            unique_releases[release_id] = release

    releases = sorted(
        unique_releases.values(),
        key=lambda item: item.get("release_date") or "1900-01-01",
        reverse=True,
    )

    total_tracks = sum(int(release.get("total_tracks") or 0) for release in releases)

    recent_raw_releases = []
    for release in releases:
        days_ago = get_release_days_ago(release.get("release_date"))
        if days_ago is not None and 0 <= days_ago <= 365:
            recent_raw_releases.append(release)

    latest_release = normalize_release(releases[0], access_token) if releases else None
    recent_releases = [normalize_release(release, access_token) for release in recent_raw_releases]

    artist_spotify_url = (
        (artist_data.get("external_urls") or {}).get("spotify")
        or f"https://open.spotify.com/artist/{artist_id}"
    )

    return {
        "artist_id": artist_data.get("id") or artist_id,
        "name": artist_data.get("name") or "Spotify Artist",
        "spotify_url": artist_spotify_url,
        "image_url": get_best_image(artist_data.get("images")),
        "genres": artist_data.get("genres") or [],
        "followers": int((artist_data.get("followers") or {}).get("total") or 0),
        "popularity": int(artist_data.get("popularity") or 0),
        "total_releases": len(releases),
        "total_tracks": total_tracks,
        "latest_release": latest_release,
        "recent_releases": recent_releases,
    }


def calculate_growth_from_snapshot_rows(snapshot_rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return {artist_id: 7-day growth} using one batched snapshot result."""
    grouped: Dict[str, Dict[date, Dict[str, Any]]] = {}

    for row in snapshot_rows:
        artist_id = str(row.get("artist_id") or "")
        raw_snapshot_date = row.get("snapshot_date")
        if not artist_id or not raw_snapshot_date:
            continue

        try:
            snapshot_day = (
                raw_snapshot_date
                if isinstance(raw_snapshot_date, date)
                else date.fromisoformat(str(raw_snapshot_date)[:10])
            )
            followers = as_int(row.get("followers"), 0)
        except Exception:
            continue

        by_day = grouped.setdefault(artist_id, {})
        existing = by_day.get(snapshot_day)
        normalized = {
            "followers": followers,
            "snapshot_date": snapshot_day,
            "created_at": row.get("created_at"),
        }
        if existing is None or str(normalized.get("created_at") or "") >= str(existing.get("created_at") or ""):
            by_day[snapshot_day] = normalized

    growth_by_artist: Dict[str, int] = {}

    for artist_id, by_day in grouped.items():
        if not by_day:
            growth_by_artist[artist_id] = 0
            continue

        latest_day = max(by_day.keys())
        window_start = latest_day - timedelta(days=7)
        eligible_days = [day for day in by_day.keys() if window_start <= day <= latest_day]
        if not eligible_days:
            growth_by_artist[artist_id] = 0
            continue

        oldest_day = min(eligible_days)
        latest_followers = as_int(by_day[latest_day].get("followers"), 0)
        oldest_followers = as_int(by_day[oldest_day].get("followers"), 0)
        growth_by_artist[artist_id] = latest_followers - oldest_followers

    return growth_by_artist


def get_artist_snapshot_data(db: Session, artist_ids: List[str]) -> Dict[str, Dict[str, int]]:
    if not artist_ids:
        return {}

    rows = db.execute(
        text(
            """
            SELECT artist_id, followers, snapshot_date, created_at
            FROM public.artist_follower_snapshots
            WHERE artist_id = ANY(:artist_ids)
            ORDER BY artist_id ASC, snapshot_date ASC, created_at ASC
            """
        ),
        {"artist_ids": artist_ids},
    ).fetchall()

    snapshot_rows = [to_dict(row) for row in rows]
    growth_by_artist = calculate_growth_from_snapshot_rows(snapshot_rows)

    latest_followers_by_artist: Dict[str, int] = {}
    latest_key_by_artist: Dict[str, str] = {}

    for row in snapshot_rows:
        artist_id = str(row.get("artist_id") or "")
        snapshot_date = row.get("snapshot_date")
        created_at = row.get("created_at")
        if not artist_id or not snapshot_date:
            continue
        sort_key = f"{str(snapshot_date)[:10]}|{created_at or ''}"
        if sort_key >= latest_key_by_artist.get(artist_id, ""):
            latest_key_by_artist[artist_id] = sort_key
            latest_followers_by_artist[artist_id] = as_int(row.get("followers"), 0)

    return {
        artist_id: {
            "followers": latest_followers_by_artist.get(artist_id, 0),
            "followers7Days": growth_by_artist.get(artist_id, 0),
        }
        for artist_id in artist_ids
    }


def serialize_artist(row: Dict[str, Any], snapshot_data: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    artist_id = row.get("artist_id")
    snapshot_data = snapshot_data or {}
    current_followers = snapshot_data.get("followers") or as_int(row.get("followers"), 0)

    return {
        "id": artist_id,
        "artistId": artist_id,
        "name": row.get("name"),
        "spotifyUrl": row.get("spotify_url"),
        "image": row.get("image_url"),
        "genres": normalize_json_value(row.get("genres"), []),
        "streams": as_int(row.get("streams"), 0),
        "growthPercent": float(row.get("growth_percent") or 0),
        "followers": current_followers,
        "followers7Days": as_int(snapshot_data.get("followers7Days"), 0),
        "popularity": as_int(row.get("popularity"), 0),
        "totalReleases": as_int(row.get("total_releases"), 0),
        "totalTracks": as_int(row.get("total_tracks"), 0),
        "latestRelease": normalize_json_value(row.get("latest_release"), None),
        "recentReleases": normalize_json_value(row.get("recent_releases"), []),
        "isActive": row.get("is_active"),
        "createdAt": str(row.get("created_at")) if row.get("created_at") is not None else None,
        "updatedAt": str(row.get("updated_at")) if row.get("updated_at") is not None else None,
    }


@router.get("")
def get_artist_library(db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM public.artist_library
                WHERE is_active IS TRUE
                ORDER BY created_at ASC NULLS LAST
                """
            )
        ).fetchall()

        artists = [to_dict(row) for row in rows]
        artist_ids = [str(artist.get("artist_id")) for artist in artists if artist.get("artist_id")]
        snapshot_by_artist = get_artist_snapshot_data(db, artist_ids)

        return {
            "success": True,
            "source": "DATABASE_URL",
            "growthLogicVersion": GROWTH_LOGIC_VERSION,
            "artists": [
                serialize_artist(artist, snapshot_by_artist.get(str(artist.get("artist_id"))))
                for artist in artists
            ],
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Artist library database error: {str(error)}")


@router.post("")
def add_artist(payload: ArtistCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        now = utc_now()
        today = date.today()

        row = db.execute(
            text(
                """
                INSERT INTO public.artist_library (
                  artist_id, name, spotify_url, image_url, genres, streams, growth_percent,
                  followers, popularity, total_releases, total_tracks, latest_release,
                  recent_releases, is_active, updated_at
                )
                VALUES (
                  :artist_id, :name, :spotify_url, :image_url, CAST(:genres AS jsonb), :streams,
                  :growth_percent, :followers, :popularity, :total_releases, :total_tracks,
                  CAST(:latest_release AS jsonb), CAST(:recent_releases AS jsonb), true, :updated_at
                )
                ON CONFLICT (artist_id) DO UPDATE SET
                  name = EXCLUDED.name,
                  spotify_url = EXCLUDED.spotify_url,
                  image_url = EXCLUDED.image_url,
                  genres = EXCLUDED.genres,
                  streams = EXCLUDED.streams,
                  growth_percent = EXCLUDED.growth_percent,
                  followers = EXCLUDED.followers,
                  popularity = EXCLUDED.popularity,
                  total_releases = EXCLUDED.total_releases,
                  total_tracks = EXCLUDED.total_tracks,
                  latest_release = EXCLUDED.latest_release,
                  recent_releases = EXCLUDED.recent_releases,
                  is_active = true,
                  updated_at = EXCLUDED.updated_at
                RETURNING *
                """
            ),
            {
                "artist_id": payload.artist_id,
                "name": payload.name,
                "spotify_url": payload.spotify_url,
                "image_url": payload.image_url,
                "genres": json_param(payload.genres),
                "streams": payload.streams,
                "growth_percent": payload.growth_percent,
                "followers": payload.followers,
                "popularity": payload.popularity,
                "total_releases": payload.total_releases,
                "total_tracks": payload.total_tracks,
                "latest_release": json_param(payload.latest_release),
                "recent_releases": json_param(payload.recent_releases),
                "updated_at": now,
            },
        ).first()

        db.execute(
            text(
                """
                INSERT INTO public.artist_follower_snapshots (artist_id, followers, snapshot_date, created_at)
                VALUES (:artist_id, :followers, :snapshot_date, :created_at)
                ON CONFLICT (artist_id, snapshot_date) DO UPDATE SET
                  followers = EXCLUDED.followers,
                  created_at = EXCLUDED.created_at
                """
            ),
            {
                "artist_id": payload.artist_id,
                "followers": payload.followers,
                "snapshot_date": today,
                "created_at": now,
            },
        )

        db.commit()
        artist = to_dict(row)
        return {"success": True, "source": "DATABASE_URL", "artist": serialize_artist(artist)}
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not add artist: {str(error)}")


@router.delete("/{artist_id}")
def remove_artist(artist_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        row = db.execute(
            text(
                """
                UPDATE public.artist_library
                SET is_active = false, updated_at = :updated_at
                WHERE artist_id = :artist_id
                RETURNING *
                """
            ),
            {"artist_id": artist_id, "updated_at": utc_now()},
        ).first()
        db.commit()
        return {
            "success": True,
            "source": "DATABASE_URL",
            "artistId": artist_id,
            "result": serialize_artist(to_dict(row)) if row else None,
        }
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not remove artist: {str(error)}")


@router.post("/sync-followers")
def sync_followers(payload: SyncFollowersRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        now = utc_now()
        today = date.today()
        artists = payload.artists or []

        if not artists:
            return {
                "success": True,
                "source": "DATABASE_URL",
                "growthLogicVersion": GROWTH_LOGIC_VERSION,
                "snapshotDate": today.isoformat(),
                "artists": [],
            }

        snapshot_rows = [
            {
                "artist_id": artist.artist_id,
                "followers": int(artist.followers or 0),
                "snapshot_date": today,
                "created_at": now,
                "updated_at": now,
            }
            for artist in artists
        ]

        db.execute(
            text(
                """
                INSERT INTO public.artist_follower_snapshots (artist_id, followers, snapshot_date, created_at)
                VALUES (:artist_id, :followers, :snapshot_date, :created_at)
                ON CONFLICT (artist_id, snapshot_date) DO UPDATE SET
                  followers = EXCLUDED.followers,
                  created_at = EXCLUDED.created_at
                """
            ),
            snapshot_rows,
        )

        db.execute(
            text(
                """
                UPDATE public.artist_library
                SET followers = :followers, updated_at = :updated_at
                WHERE artist_id = :artist_id
                """
            ),
            snapshot_rows,
        )

        artist_ids = [row["artist_id"] for row in snapshot_rows]
        snapshot_by_artist = get_artist_snapshot_data(db, artist_ids)
        db.commit()

        return {
            "success": True,
            "source": "DATABASE_URL",
            "growthLogicVersion": GROWTH_LOGIC_VERSION,
            "snapshotDate": today.isoformat(),
            "artists": [
                {
                    "artistId": artist_id,
                    "followers": snapshot_by_artist.get(artist_id, {}).get("followers", 0),
                    "followers7Days": snapshot_by_artist.get(artist_id, {}).get("followers7Days", 0),
                }
                for artist_id in artist_ids
            ],
        }
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not sync followers: {str(error)}")


@router.post("/sync-metadata")
def sync_metadata(db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        access_token = get_spotify_access_token()
        rows = db.execute(
            text(
                """
                SELECT *
                FROM public.artist_library
                WHERE is_active IS TRUE
                ORDER BY created_at ASC NULLS LAST
                """
            )
        ).fetchall()
        artists = [to_dict(row) for row in rows]

        today = date.today()
        now = utc_now()
        results = []
        synced_count = 0
        failed_count = 0
        pushed_online_ids: set[int] = set()

        for artist in artists:
            artist_id = artist.get("artist_id")
            try:
                metadata = fetch_spotify_artist_metadata(
                    artist_id=artist_id,
                    access_token=access_token,
                )

                db.execute(
                    text(
                        """
                        UPDATE public.artist_library
                        SET name = :name,
                            spotify_url = :spotify_url,
                            image_url = :image_url,
                            genres = CAST(:genres AS jsonb),
                            followers = :followers,
                            popularity = :popularity,
                            total_releases = :total_releases,
                            total_tracks = :total_tracks,
                            latest_release = CAST(:latest_release AS jsonb),
                            recent_releases = CAST(:recent_releases AS jsonb),
                            updated_at = :updated_at
                        WHERE artist_id = :artist_id
                        """
                    ),
                    {
                        "artist_id": artist_id,
                        "name": metadata["name"],
                        "spotify_url": metadata["spotify_url"],
                        "image_url": metadata["image_url"],
                        "genres": json_param(metadata["genres"]),
                        "followers": metadata["followers"],
                        "popularity": metadata["popularity"],
                        "total_releases": metadata["total_releases"],
                        "total_tracks": metadata["total_tracks"],
                        "latest_release": json_param(metadata["latest_release"]),
                        "recent_releases": json_param(metadata["recent_releases"]),
                        "updated_at": now,
                    },
                )

                db.execute(
                    text(
                        """
                        INSERT INTO public.artist_follower_snapshots (artist_id, followers, snapshot_date, created_at)
                        VALUES (:artist_id, :followers, :snapshot_date, :created_at)
                        ON CONFLICT (artist_id, snapshot_date) DO UPDATE SET
                          followers = EXCLUDED.followers,
                          created_at = EXCLUDED.created_at
                        """
                    ),
                    {
                        "artist_id": artist_id,
                        "followers": metadata["followers"],
                        "snapshot_date": today,
                        "created_at": now,
                    },
                )

                updated_schedule_ids = push_matching_scheduling_rows_online(
                    db=db,
                    artist_name=metadata["name"],
                    metadata=metadata,
                )
                pushed_online_ids.update(updated_schedule_ids)
                db.commit()

                synced_count += 1
                results.append(
                    {
                        "artistId": artist_id,
                        "name": metadata["name"],
                        "ok": True,
                        "followers": metadata["followers"],
                        "totalReleases": metadata["total_releases"],
                        "totalTracks": metadata["total_tracks"],
                        "recentReleases": len(metadata["recent_releases"]),
                        "spotifyAlbumsMatched": len(
                            build_spotify_release_references(metadata)["albums"]
                        ),
                        "spotifySongsMatched": len(
                            build_spotify_release_references(metadata)["songs"]
                        ),
                        "pushedOnline": len(updated_schedule_ids),
                        "pushedOnlineIds": updated_schedule_ids,
                        "message": "Artist metadata synced",
                    }
                )
            except Exception as artist_error:
                db.rollback()
                failed_count += 1
                results.append(
                    {
                        "artistId": artist_id,
                        "name": artist.get("name"),
                        "ok": False,
                        "message": str(artist_error),
                    }
                )

        return {
            "success": True,
            "source": "DATABASE_URL",
            "growthLogicVersion": GROWTH_LOGIC_VERSION,
            "total": len(artists),
            "synced": synced_count,
            "failed": failed_count,
            "pushedOnline": len(pushed_online_ids),
            "pushedOnlineIds": sorted(pushed_online_ids),
            "snapshotDate": today.isoformat(),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not sync artist metadata: {str(error)}")
