import base64
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from supabase import Client, create_client


router = APIRouter(prefix="/api/artist-library", tags=["artist-library"])


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"


def get_supabase() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_URL")

    if not supabase_key:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_SERVICE_ROLE_KEY")

    return create_client(supabase_url, supabase_key)


def get_spotify_access_token() -> str:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET",
        )

    raw_token = f"{client_id}:{client_secret}".encode("utf-8")
    basic_token = base64.b64encode(raw_token).decode("utf-8")

    with httpx.Client(timeout=30) as client:
        response = client.post(
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Spotify token request failed: {response.text}",
        )

    data = response.json()
    access_token = data.get("access_token")

    if not access_token:
        raise HTTPException(status_code=500, detail="Spotify did not return an access token")

    return access_token


def spotify_get(url: str, access_token: str) -> Dict[str, Any]:
    with httpx.Client(timeout=30) as client:
        response = client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code >= 400:
        raise RuntimeError(f"Spotify request failed: {response.text}")

    return response.json()


def get_best_image(images: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    if not images:
        return None

    return images[0].get("url")


def normalize_release(release: Dict[str, Any]) -> Dict[str, Any]:
    release_id = release.get("id")

    return {
        "id": release_id,
        "name": release.get("name"),
        "image": get_best_image(release.get("images") or []),
        "spotifyUrl": (release.get("external_urls") or {}).get("spotify")
        or (f"https://open.spotify.com/album/{release_id}" if release_id else None),
        "releaseDate": release.get("release_date"),
        "totalTracks": release.get("total_tracks") or 0,
    }


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
                current_followers = int(snapshot_rows[0].get("followers") or current_followers)

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

        return {"success": True, "artists": enriched_artists}

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
        raise HTTPException(status_code=500, detail=f"Could not add artist: {str(error)}")


@router.post("/sync-metadata")
def sync_artist_metadata() -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        access_token = get_spotify_access_token()
        today = date.today().isoformat()
        now = datetime.utcnow().isoformat()

        response = (
            supabase.table("artist_library")
            .select("artist_id,name")
            .eq("is_active", True)
            .order("created_at", desc=False)
            .execute()
        )

        artists = response.data or []
        results = []
        synced = 0
        failed = 0

        for saved_artist in artists:
            artist_id = saved_artist.get("artist_id")

            if not artist_id:
                continue

            try:
                artist_data = spotify_get(
                    f"{SPOTIFY_API_BASE_URL}/artists/{artist_id}",
                    access_token,
                )

                albums_data = spotify_get(
                    f"{SPOTIFY_API_BASE_URL}/artists/{artist_id}/albums"
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
                    key=lambda release: release.get("release_date") or "1900-01-01",
                    reverse=True,
                )

                total_releases = len(releases)
                total_tracks = sum(int(release.get("total_tracks") or 0) for release in releases)
                latest_release = normalize_release(releases[0]) if releases else None
                followers = int((artist_data.get("followers") or {}).get("total") or 0)

                artist_payload = {
                    "name": artist_data.get("name") or saved_artist.get("name"),
                    "spotify_url": (artist_data.get("external_urls") or {}).get("spotify")
                    or f"https://open.spotify.com/artist/{artist_id}",
                    "image_url": get_best_image(artist_data.get("images") or []),
                    "genres": artist_data.get("genres") or [],
                    "followers": followers,
                    "popularity": artist_data.get("popularity") or 0,
                    "total_releases": total_releases,
                    "total_tracks": total_tracks,
                    "latest_release": latest_release,
                    "is_active": True,
                    "updated_at": now,
                }

                supabase.table("artist_library").update(artist_payload).eq(
                    "artist_id",
                    artist_id,
                ).execute()

                supabase.table("artist_follower_snapshots").upsert(
                    {
                        "artist_id": artist_id,
                        "followers": followers,
                        "snapshot_date": today,
                    },
                    on_conflict="artist_id,snapshot_date",
                ).execute()

                synced += 1
                results.append(
                    {
                        "artistId": artist_id,
                        "name": artist_payload["name"],
                        "ok": True,
                        "message": "Artist metadata synced",
                    }
                )

            except Exception as artist_error:
                failed += 1
                results.append(
                    {
                        "artistId": artist_id,
                        "name": saved_artist.get("name"),
                        "ok": False,
                        "error": str(artist_error),
                    }
                )

        return {
            "success": failed == 0,
            "total": len(artists),
            "synced": synced,
            "failed": failed,
            "snapshotDate": today,
            "results": results,
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not sync artist metadata: {str(error)}")


@router.delete("/{artist_id}")
def remove_artist(artist_id: str) -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        now = datetime.utcnow().isoformat()

        response = (
            supabase.table("artist_library")
            .update({"is_active": False, "updated_at": now})
            .eq("artist_id", artist_id)
            .execute()
        )

        return {"success": True, "artistId": artist_id, "result": response.data}

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not remove artist: {str(error)}")


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

        return {"success": True, "snapshotDate": today, "artists": synced}

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not sync followers: {str(error)}")
