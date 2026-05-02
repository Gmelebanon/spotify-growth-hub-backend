import os
from datetime import datetime, timedelta
from typing import List

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.follower_history import FollowerHistory
from app.models.playlist import Playlist
from app.models.spotify_account import SpotifyAccount

router = APIRouter(tags=["playlists"])


def get_account_or_404(db: Session, account_id: int):
    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def get_playlist_or_404(db: Session, account_id: int, playlist_id: int):
    playlist = (
        db.query(Playlist)
        .filter(Playlist.id == playlist_id, Playlist.account_id == account_id)
        .first()
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


def safe_set(model, field: str, value):
    if hasattr(model, field):
        setattr(model, field, value)


def refresh_spotify_access_token(db: Session, account: SpotifyAccount) -> str:
    if not account.refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Spotify access token expired. Reconnect this Spotify account.",
        )

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET in backend .env",
        )

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=20,
    )

    if not response.ok:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to refresh Spotify token: {response.text}",
        )

    data = response.json()
    access_token = data.get("access_token")

    if not access_token:
        raise HTTPException(status_code=401, detail="Spotify did not return access token")

    account.access_token = access_token

    if data.get("refresh_token"):
        account.refresh_token = data["refresh_token"]

    db.add(account)
    db.commit()
    db.refresh(account)

    return access_token


def spotify_request(db: Session, account: SpotifyAccount, method: str, url: str, **kwargs):
    access_token = account.access_token or refresh_spotify_access_token(db, account)
    headers = kwargs.pop("headers", {})
    timeout = kwargs.pop("timeout", 30)

    response = requests.request(
        method,
        url,
        headers={**headers, "Authorization": f"Bearer {access_token}"},
        timeout=timeout,
        **kwargs,
    )

    if response.status_code == 401:
        access_token = refresh_spotify_access_token(db, account)
        response = requests.request(
            method,
            url,
            headers={**headers, "Authorization": f"Bearer {access_token}"},
            timeout=timeout,
            **kwargs,
        )

    return response


def get_history_rows(db: Session, playlist_id: int, limit: int | None = None):
    query = (
        db.query(FollowerHistory)
        .filter(FollowerHistory.playlist_id == playlist_id)
        .order_by(FollowerHistory.created_at.desc())
    )

    if limit:
        query = query.limit(limit)

    return query.all()


def closest_followers_at_or_before(history_rows, target: datetime):
    for row in history_rows:
        if row.created_at and row.created_at <= target:
            return row.followers or 0
    return None


def compute_growth_stats(playlist: Playlist, history_rows):
    now = datetime.utcnow()
    current = getattr(playlist, "followers", 0) or 0

    followers_24h = closest_followers_at_or_before(history_rows, now - timedelta(days=1))
    followers_7d = closest_followers_at_or_before(history_rows, now - timedelta(days=7))
    followers_30d = closest_followers_at_or_before(history_rows, now - timedelta(days=30))

    growth_24h = current - followers_24h if followers_24h is not None else 0
    growth_7d = current - followers_7d if followers_7d is not None else 0
    growth_30d = current - followers_30d if followers_30d is not None else 0

    return {
        "followers": current,
        "growth": growth_24h,
        "growth_24h": growth_24h,
        "growth_7d": growth_7d,
        "growth_30d": growth_30d,
    }


def serialize_playlist(playlist: Playlist, history_rows=None):
    spotify_id = getattr(playlist, "spotify_id", None)
    history_rows = history_rows or []
    growth = compute_growth_stats(playlist, history_rows)

    tracks_count = (
        getattr(playlist, "tracks_count", 0)
        or getattr(playlist, "tracks_total", 0)
        or getattr(playlist, "total_tracks", 0)
        or 0
    )

    return {
        "id": playlist.id,
        "account_id": playlist.account_id,
        "spotify_id": spotify_id,
        "spotify_playlist_id": spotify_id,
        "name": getattr(playlist, "name", "Untitled Playlist"),
        "description": getattr(playlist, "description", None),
        "followers": growth["followers"],
        "growth": growth["growth"],
        "growth_24h": growth["growth_24h"],
        "growth_7d": growth["growth_7d"],
        "growth_30d": growth["growth_30d"],
        "tracks_count": tracks_count,
        "tracks_total": tracks_count,
        "total_tracks": tracks_count,
        "genre": getattr(playlist, "genre", None),
        "image_url": getattr(playlist, "image_url", None),
        "spotify_url": getattr(playlist, "spotify_url", None)
        or (f"https://open.spotify.com/playlist/{spotify_id}" if spotify_id else None),
        "updated_at": playlist.updated_at.isoformat()
        if getattr(playlist, "updated_at", None)
        else None,
        "created_at": playlist.created_at.isoformat()
        if getattr(playlist, "created_at", None)
        else None,
    }


def fetch_spotify_account_playlists(db: Session, account: SpotifyAccount):
    all_items = []
    url = "https://api.spotify.com/v1/me/playlists"

    while url:
        response = spotify_request(
            db,
            account,
            "GET",
            url,
            params={"limit": 50},  # max allowed
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify playlists fetch failed: {response.text}",
            )

        data = response.json()

        items = data.get("items", [])
        all_items.extend(items)

        # 🔥 THIS IS THE KEY FIX
        url = data.get("next")

    return all_items


def fetch_spotify_playlist_detail(db: Session, account: SpotifyAccount, spotify_playlist_id: str):
    response = spotify_request(
        db,
        account,
        "GET",
        f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}",
        params={"fields": "id,name,description,followers,total,images,tracks.total,external_urls"},
        timeout=30,
    )

    if not response.ok:
        return None

    return response.json()


def update_playlist_from_spotify_item(playlist: Playlist, item: dict, detail: dict | None = None):
    source = detail or item

    images = source.get("images") or []
    image_url = images[0].get("url") if images else None
    tracks_total = (source.get("tracks") or {}).get("total") or 0
    external_urls = source.get("external_urls") or {}
    spotify_url = external_urls.get("spotify")
    followers = (source.get("followers") or {}).get("total")

    safe_set(playlist, "name", source.get("name") or item.get("name") or "Untitled Playlist")
    safe_set(playlist, "description", source.get("description") or item.get("description"))
    safe_set(playlist, "image_url", image_url)
    safe_set(playlist, "spotify_url", spotify_url)
    safe_set(playlist, "external_url", spotify_url)
    safe_set(playlist, "tracks_count", tracks_total)
    safe_set(playlist, "tracks_total", tracks_total)
    safe_set(playlist, "total_tracks", tracks_total)

    if followers is not None:
      safe_set(playlist, "followers", followers)

    safe_set(playlist, "updated_at", datetime.utcnow())


def refresh_account_playlists_from_spotify(db: Session, account_id: int):
    account = get_account_or_404(db, account_id)
    spotify_items = fetch_spotify_account_playlists(db, account)

    imported = 0
    updated = 0
    now = datetime.utcnow()

    for item in spotify_items:
        spotify_id = item.get("id")
        if not spotify_id:
            continue

        playlist = (
            db.query(Playlist)
            .filter(Playlist.account_id == account_id, Playlist.spotify_id == spotify_id)
            .first()
        )

        if playlist:
            updated += 1
        else:
            playlist = Playlist(
                account_id=account_id,
                spotify_id=spotify_id,
                name=item.get("name") or "Untitled Playlist",
            )
            imported += 1

        detail = fetch_spotify_playlist_detail(db, account, spotify_id)
        update_playlist_from_spotify_item(playlist, item, detail)

        db.add(playlist)
        db.flush()

        try:
            history = FollowerHistory(
                playlist_id=playlist.id,
                followers=getattr(playlist, "followers", 0) or 0,
                created_at=now,
            )
            db.add(history)
            db.flush()
        except Exception:
            db.rollback()
            db.begin()

    db.commit()

    return {"imported": imported, "updated": updated, "total": len(spotify_items)}


def fetch_playlist_tracks_from_spotify(db: Session, account: SpotifyAccount, spotify_playlist_id: str):
    tracks = []
    offset = 0
    limit = 100

    while True:
        response = spotify_request(
            db,
            account,
            "GET",
            f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}/tracks",
            params={"limit": limit, "offset": offset},
            timeout=30,
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify track fetch failed: {response.text}",
            )

        payload = response.json()

        for item in payload.get("items") or []:
            track = item.get("track") or {}
            if not track:
                continue

            artists = track.get("artists") or []
            album = track.get("album") or {}
            images = album.get("images") or []
            external_urls = track.get("external_urls") or {}

            tracks.append(
                {
                    "id": track.get("id") or f"track-{len(tracks)}",
                    "spotify_id": track.get("id"),
                    "name": track.get("name") or "Untitled Track",
                    "title": track.get("name") or "Untitled Track",
                    "artist_name": ", ".join(
                        [artist.get("name") for artist in artists if artist.get("name")]
                    ),
                    "artist": ", ".join(
                        [artist.get("name") for artist in artists if artist.get("name")]
                    ),
                    "album_name": album.get("name"),
                    "image_url": images[0].get("url") if images else None,
                    "spotify_url": external_urls.get("spotify"),
                }
            )

        if not payload.get("next"):
            break

        offset += limit

    return tracks


@router.get("/api/accounts/{account_id}/playlists")
def get_playlists_api(account_id: int, db: Session = Depends(get_db)):
    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.name.asc())
        .all()
    )

    items = [serialize_playlist(playlist, get_history_rows(db, playlist.id)) for playlist in playlists]
    return {"items": items, "playlists": items}


@router.get("/accounts/{account_id}/playlists")
def get_playlists_legacy(account_id: int, db: Session = Depends(get_db)):
    return get_playlists_api(account_id, db)


@router.get("/api/accounts/{account_id}/playlists/{playlist_id}")
def get_playlist_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    playlist = get_playlist_or_404(db, account_id, playlist_id)
    history_rows = get_history_rows(db, playlist.id)
    return serialize_playlist(playlist, history_rows)


@router.post("/api/accounts/{account_id}/playlists/sync")
def sync_account_playlists_api(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    result = refresh_account_playlists_from_spotify(db, account_id)

    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "message": "Spotify playlists refreshed",
        "imported": result["imported"],
        "updated": result["updated"],
        "total": result["total"],
        "items": [serialize_playlist(playlist, get_history_rows(db, playlist.id)) for playlist in playlists],
    }

@router.post("/accounts/{account_id}/playlists/sync")
def sync_account_playlists_legacy(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return sync_account_playlists_api(account_id, limit, offset, db)

@router.get("/accounts/{account_id}/playlists/{playlist_id}")
def get_playlist_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return get_playlist_api(account_id, playlist_id, db)


@router.get("/api/accounts/{account_id}/playlists/{playlist_id}/history")
def get_playlist_history_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    playlist = get_playlist_or_404(db, account_id, playlist_id)
    history_rows = get_history_rows(db, playlist.id, limit=60)

    items = [
        {
            "id": row.id,
            "playlist_id": row.playlist_id,
            "followers": row.followers or 0,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "date": row.created_at.isoformat() if row.created_at else None,
        }
        for row in history_rows
    ]

    return {
        "playlist": serialize_playlist(playlist, history_rows),
        "items": items,
        "history": items,
    }


@router.get("/accounts/{account_id}/playlists/{playlist_id}/history")
def get_playlist_history_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return get_playlist_history_api(account_id, playlist_id, db)


@router.get("/api/accounts/{account_id}/playlists/{playlist_id}/tracks")
def get_playlist_tracks_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    account = get_account_or_404(db, account_id)
    playlist = get_playlist_or_404(db, account_id, playlist_id)

    spotify_id = getattr(playlist, "spotify_id", None)
    if not spotify_id:
        raise HTTPException(status_code=400, detail="No Spotify playlist ID")

    tracks = fetch_playlist_tracks_from_spotify(db, account, spotify_id)

    safe_set(playlist, "tracks_count", len(tracks))
    safe_set(playlist, "tracks_total", len(tracks))
    safe_set(playlist, "total_tracks", len(tracks))
    safe_set(playlist, "updated_at", datetime.utcnow())

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "playlist": serialize_playlist(playlist, get_history_rows(db, playlist.id)),
        "items": tracks,
        "tracks": tracks,
    }


@router.get("/accounts/{account_id}/playlists/{playlist_id}/tracks")
def get_playlist_tracks_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return get_playlist_tracks_api(account_id, playlist_id, db)

@router.post("/api/accounts/{account_id}/playlists/{playlist_id}/sync")
def sync_playlist_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    account = get_account_or_404(db, account_id)
    playlist = get_playlist_or_404(db, account_id, playlist_id)

    spotify_id = getattr(playlist, "spotify_id", None)
    if spotify_id:
        detail = fetch_spotify_playlist_detail(db, account, spotify_id)
        if detail:
            update_playlist_from_spotify_item(playlist, detail, detail)

    now = datetime.utcnow()
    history = FollowerHistory(
        playlist_id=playlist.id,
        followers=getattr(playlist, "followers", 0) or 0,
        created_at=now,
    )

    try:
        db.add(playlist)
        db.add(history)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Failed to sync playlist history")

    db.refresh(playlist)
    return {"message": "Playlist synced", "playlist": serialize_playlist(playlist, get_history_rows(db, playlist.id))}


@router.post("/accounts/{account_id}/playlists/{playlist_id}/sync")
def sync_playlist_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return sync_playlist_api(account_id, playlist_id, db)


class ReplacePlaylistTrackPayload(BaseModel):
    id: str | None = None
    spotify_id: str | None = None
    title: str | None = None
    name: str | None = None
    artist: str | None = None
    artist_name: str | None = None


class ReplacePlaylistTracksRequest(BaseModel):
    tracks: List[ReplacePlaylistTrackPayload]


def resolve_track_uri(db: Session, track: ReplacePlaylistTrackPayload, account: SpotifyAccount):
    if track.spotify_id:
        return f"spotify:track:{track.spotify_id}"

    if track.id and len(track.id) >= 16 and not track.id.startswith(("track-", "typed-", "curation-")):
        return f"spotify:track:{track.id}"

    title = track.title or track.name
    artist = track.artist or track.artist_name

    if not title:
        return None

    query = f"{title} {artist}" if artist else title

    response = spotify_request(
        db,
        account,
        "GET",
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "track", "limit": 1},
    )

    if not response.ok:
        return None

    items = response.json().get("tracks", {}).get("items", [])
    if not items:
        return None

    spotify_id = items[0].get("id")
    if not spotify_id:
        return None

    return f"spotify:track:{spotify_id}"


def clear_playlist_on_spotify(db: Session, account: SpotifyAccount, spotify_playlist_id: str):
    response = spotify_request(
        db,
        account,
        "PUT",
        f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}/tracks",
        json={"uris": []},
    )

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Spotify clear playlist failed: {response.text}",
        )


def add_tracks_to_spotify_playlist(db: Session, account: SpotifyAccount, spotify_playlist_id: str, track_uris: list[str]):
    for index in range(0, len(track_uris), 100):
        chunk = track_uris[index:index + 100]

        response = spotify_request(
            db,
            account,
            "POST",
            f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}/tracks",
            json={"uris": chunk},
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify add tracks failed: {response.text}",
            )


def replace_playlist_tracks_logic(account_id: int, playlist_id: int, payload: ReplacePlaylistTracksRequest, db: Session):
    account = get_account_or_404(db, account_id)
    playlist = get_playlist_or_404(db, account_id, playlist_id)

    spotify_playlist_id = getattr(playlist, "spotify_id", None)

    if not spotify_playlist_id:
        raise HTTPException(status_code=400, detail="No Spotify playlist ID")

    track_uris = []
    unresolved = []

    for track in payload.tracks:
        uri = resolve_track_uri(db, track, account)
        if uri:
            track_uris.append(uri)
        else:
            unresolved.append(
                {
                    "title": track.title or track.name,
                    "artist": track.artist or track.artist_name,
                }
            )

    if not track_uris:
        raise HTTPException(status_code=400, detail="No valid tracks found")

    clear_playlist_on_spotify(db, account, spotify_playlist_id)
    add_tracks_to_spotify_playlist(db, account, spotify_playlist_id, track_uris)

    safe_set(playlist, "tracks_count", len(track_uris))
    safe_set(playlist, "tracks_total", len(track_uris))
    safe_set(playlist, "total_tracks", len(track_uris))
    safe_set(playlist, "updated_at", datetime.utcnow())

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "message": "Playlist cleared and replaced successfully",
        "playlist": serialize_playlist(playlist, get_history_rows(db, playlist.id)),
        "synced_tracks": len(track_uris),
        "unresolved_tracks": unresolved,
    }


@router.post("/api/accounts/{account_id}/playlists/{playlist_id}/replace-tracks")
def replace_playlist_tracks(
    account_id: int,
    playlist_id: int,
    payload: ReplacePlaylistTracksRequest,
    db: Session = Depends(get_db),
):
    return replace_playlist_tracks_logic(account_id, playlist_id, payload, db)


@router.post("/accounts/{account_id}/playlists/{playlist_id}/replace-tracks")
def replace_playlist_tracks_legacy(
    account_id: int,
    playlist_id: int,
    payload: ReplacePlaylistTracksRequest,
    db: Session = Depends(get_db),
):
    return replace_playlist_tracks_logic(account_id, playlist_id, payload, db)

class UpdatePlaylistGenreRequest(BaseModel):
    genre: str | None = None


@router.patch("/api/playlists/{playlist_id}/genre")
def update_playlist_genre(
    playlist_id: int,
    payload: UpdatePlaylistGenreRequest,
    db: Session = Depends(get_db),
):
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist.genre = payload.genre
    playlist.updated_at = datetime.utcnow()

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "message": "Genre updated",
        "playlist_id": playlist.id,
        "genre": playlist.genre,
    }