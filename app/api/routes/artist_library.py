import base64
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
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
        raise HTTPException(status_code=500, detail="Missing SUPABASE_SERVICE_ROLE_KEY")

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

    data = response.json()
    return data["access_token"]


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

    albums_data = spotify_get(
        f"https://api.spotify.com/v1/artists/{artist_id}/albums"
        "?include_groups=album,single,appears_on,compilation&market=US&limit=50",
        access_token,
    )

    unique_releases: Dict[str, Dict[str, Any]] = {}

    for release in albums_data.get("items") or []:
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
        if days_ago is not None and 0 <= days_ago <= 7:
            recent_raw_releases.append(release)

    latest_release = normalize_release(releases[0], access_token) if releases else None
    recent_releases = [
        normalize_release(release, access_token)
        for release in recent_raw_releases
    ]

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
                    "recentReleases": artist.get("recent_releases") or [],
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
            "recent_releases": payload.recent_releases,
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


@router.post("/sync-metadata")
def sync_metadata() -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        access_token = get_spotify_access_token()

        response = (
            supabase.table("artist_library")
            .select("*")
            .eq("is_active", True)
            .order("created_at", desc=False)
            .execute()
        )

        artists = response.data or []
        today = date.today().isoformat()
        results = []
        synced_count = 0
        failed_count = 0

        for artist in artists:
            artist_id = artist.get("artist_id")

            try:
                metadata = fetch_spotify_artist_metadata(
                    artist_id=artist_id,
                    access_token=access_token,
                )

                update_payload = {
                    "name": metadata["name"],
                    "spotify_url": metadata["spotify_url"],
                    "image_url": metadata["image_url"],
                    "genres": metadata["genres"],
                    "followers": metadata["followers"],
                    "popularity": metadata["popularity"],
                    "total_releases": metadata["total_releases"],
                    "total_tracks": metadata["total_tracks"],
                    "latest_release": metadata["latest_release"],
                    "recent_releases": metadata["recent_releases"],
                    "updated_at": datetime.utcnow().isoformat(),
                }

                supabase.table("artist_library").update(update_payload).eq(
                    "artist_id",
                    artist_id,
                ).execute()

                snapshot_payload = {
                    "artist_id": artist_id,
                    "followers": metadata["followers"],
                    "snapshot_date": today,
                }

                supabase.table("artist_follower_snapshots").upsert(
                    snapshot_payload,
                    on_conflict="artist_id,snapshot_date",
                ).execute()

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
                        "message": "Artist metadata synced",
                    }
                )

            except Exception as artist_error:
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
            "total": len(artists),
            "synced": synced_count,
            "failed": failed_count,
            "snapshotDate": today,
            "results": results,
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not sync artist metadata: {str(error)}",
        )
