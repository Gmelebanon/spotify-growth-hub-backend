from datetime import date

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.spotify_account import SpotifyAccount
from app.models.playlist import Playlist
from app.models.follower_history import FollowerHistory


SPOTIFY_ME_PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists"
SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"


def get_account_or_404(db: Session, account_id: int) -> SpotifyAccount:
    account = (
        db.query(SpotifyAccount)
        .filter(SpotifyAccount.id == account_id)
        .first()
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Spotify account not found.")
    return account


def refresh_access_token_for_account(db: Session, account: SpotifyAccount) -> str:
    try:
        response = httpx.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": account.refresh_token,
            },
            auth=(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET),
            timeout=15,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Spotify token endpoint: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify token refresh failed ({response.status_code}): {response.text}",
        )

    data = response.json()
    new_access_token = data.get("access_token")

    if not new_access_token:
        raise HTTPException(
            status_code=502,
            detail="Spotify refresh response is missing access_token.",
        )

    account.access_token = new_access_token

    new_refresh_token = data.get("refresh_token")
    if new_refresh_token:
        account.refresh_token = new_refresh_token

    db.commit()
    db.refresh(account)

    return new_access_token


def _spotify_get_with_auto_refresh(
    db: Session,
    account: SpotifyAccount,
    url: str,
    params: dict | None = None,
) -> httpx.Response:
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {account.access_token}"},
            params=params or {},
            timeout=15,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Spotify endpoint: {exc}",
        ) from exc

    if response.status_code == 401:
        new_token = refresh_access_token_for_account(db, account)

        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {new_token}"},
                params=params or {},
                timeout=15,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach Spotify endpoint after refresh: {exc}",
            ) from exc

    return response


def fetch_spotify_playlists_for_account(
    db: Session,
    account_id: int,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    account = get_account_or_404(db, account_id)

    response = _spotify_get_with_auto_refresh(
        db=db,
        account=account,
        url=SPOTIFY_ME_PLAYLISTS_URL,
        params={"limit": limit, "offset": offset},
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify playlists fetch failed ({response.status_code}): {response.text}",
        )

    data = response.json()
    items = data.get("items", [])

    results = []
    for item in items:
        images = item.get("images") or []
        owner = item.get("owner") or {}
        tracks = item.get("tracks") or {}
        external_urls = item.get("external_urls") or {}

        results.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "public": item.get("public"),
                "owner_id": owner.get("id"),
                "owner_display_name": owner.get("display_name"),
                "tracks_total": tracks.get("total", 0),
                "spotify_url": external_urls.get("spotify"),
                "image_url": images[0].get("url") if images else None,
            }
        )

    return {
        "limit": data.get("limit", limit),
        "offset": data.get("offset", offset),
        "total": data.get("total", len(results)),
        "results": results,
    }


def sync_spotify_playlists_to_db(
    db: Session,
    account_id: int,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    spotify_data = fetch_spotify_playlists_for_account(
        db=db,
        account_id=account_id,
        limit=limit,
        offset=offset,
    )

    synced = 0
    created = 0
    updated = 0

    for item in spotify_data["results"]:
        playlist = (
            db.query(Playlist)
            .filter(Playlist.spotify_playlist_id == item["id"])
            .first()
        )

        if playlist is None:
            playlist = Playlist(
                account_id=account_id,
                spotify_playlist_id=item["id"],
                name=item["name"] or "Untitled Playlist",
                description=item.get("description"),
                followers=0,
                tracks_total=item.get("tracks_total", 0),
                public=item.get("public"),
                owner_id=item.get("owner_id"),
                owner_display_name=item.get("owner_display_name"),
                spotify_url=item.get("spotify_url"),
                image_url=item.get("image_url"),
            )
            db.add(playlist)
            created += 1
        else:
            playlist.account_id = account_id
            playlist.name = item["name"] or playlist.name
            playlist.description = item.get("description")
            playlist.tracks_total = item.get("tracks_total", 0)
            playlist.public = item.get("public")
            playlist.owner_id = item.get("owner_id")
            playlist.owner_display_name = item.get("owner_display_name")
            playlist.spotify_url = item.get("spotify_url")
            playlist.image_url = item.get("image_url")
            updated += 1

        synced += 1

    db.commit()

    return {
        "message": "Spotify playlists synced successfully.",
        "account_id": account_id,
        "synced": synced,
        "created": created,
        "updated": updated,
        "spotify_total": spotify_data["total"],
        "results": spotify_data["results"],
    }


def fetch_spotify_playlist_details(
    db: Session,
    account_id: int,
    spotify_playlist_id: str,
) -> dict:
    account = get_account_or_404(db, account_id)

    response = _spotify_get_with_auto_refresh(
        db=db,
        account=account,
        url=f"{SPOTIFY_PLAYLIST_URL}/{spotify_playlist_id}",
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify playlist details fetch failed ({response.status_code}): {response.text}",
        )

    return response.json()


def sync_followers_history_for_account(db: Session, account_id: int) -> dict:
    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.id.asc())
        .all()
    )

    if not playlists:
        return {
            "message": "No playlists found for this account.",
            "account_id": account_id,
            "synced": 0,
            "results": [],
        }

    today = date.today()
    synced = 0
    created_history_rows = 0
    updated_playlists = 0
    results = []

    for playlist in playlists:
        details = fetch_spotify_playlist_details(
            db=db,
            account_id=account_id,
            spotify_playlist_id=playlist.spotify_playlist_id,
        )

        followers_obj = details.get("followers") or {}
        tracks_obj = details.get("tracks") or {}

        latest_followers = followers_obj.get("total", 0)
        latest_tracks_total = tracks_obj.get("total", 0)

        playlist.followers = latest_followers
        playlist.tracks_total = latest_tracks_total
        updated_playlists += 1

        existing_history = (
            db.query(FollowerHistory)
            .filter(
                FollowerHistory.playlist_id == playlist.id,
                FollowerHistory.date == today,
            )
            .first()
        )

        if existing_history is None:
            history_row = FollowerHistory(
                playlist_id=playlist.id,
                date=today,
                followers=latest_followers,
            )
            db.add(history_row)
            created_history_rows += 1
        else:
            existing_history.followers = latest_followers

        synced += 1
        results.append(
            {
                "playlist_id": playlist.id,
                "spotify_playlist_id": playlist.spotify_playlist_id,
                "name": playlist.name,
                "followers": latest_followers,
                "tracks_total": latest_tracks_total,
            }
        )

    db.commit()

    return {
        "message": "Follower history synced successfully.",
        "account_id": account_id,
        "synced": synced,
        "updated_playlists": updated_playlists,
        "created_history_rows": created_history_rows,
        "results": results,
    }


def fetch_spotify_playlist_tracks(
    db: Session,
    account_id: int,
    spotify_playlist_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    account = get_account_or_404(db, account_id)

    response = _spotify_get_with_auto_refresh(
        db=db,
        account=account,
        url=f"{SPOTIFY_PLAYLIST_URL}/{spotify_playlist_id}/tracks",
        params={"limit": limit, "offset": offset},
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify playlist tracks fetch failed ({response.status_code}): {response.text}",
        )

    data = response.json()
    items = data.get("items", [])

    results = []

    for item in items:
        track = item.get("track") or {}
        album = track.get("album") or {}
        artists = track.get("artists") or []
        external_urls = track.get("external_urls") or {}
        images = album.get("images") or []

        results.append(
            {
                "track_id": track.get("id"),
                "track_name": track.get("name"),
                "artist_names": [
                    artist.get("name")
                    for artist in artists
                    if artist.get("name")
                ],
                "album_name": album.get("name"),
                "image_url": images[0].get("url") if images else None,
                "spotify_url": external_urls.get("spotify"),
                "duration_ms": track.get("duration_ms"),
                "preview_url": track.get("preview_url"),
            }
        )

    return {
        "limit": data.get("limit", limit),
        "offset": data.get("offset", offset),
        "total": data.get("total", len(results)),
        "results": results,
    }