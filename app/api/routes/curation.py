import base64
import os
import re
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.spotify_account import SpotifyAccount

router = APIRouter(prefix="/api", tags=["curation"])


class ImportSpotifyLinkRequest(BaseModel):
    link: str


def get_account_or_404(db: Session, account_id: int) -> SpotifyAccount:
    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    return account


def extract_spotify_resource(link: str) -> tuple[str, str]:
    value = link.strip()

    playlist_match = re.search(r"playlist/([A-Za-z0-9]+)", value)
    if playlist_match:
        return "playlist", playlist_match.group(1)

    album_match = re.search(r"album/([A-Za-z0-9]+)", value)
    if album_match:
        return "album", album_match.group(1)

    playlist_uri_match = re.search(r"spotify:playlist:([A-Za-z0-9]+)", value)
    if playlist_uri_match:
        return "playlist", playlist_uri_match.group(1)

    album_uri_match = re.search(r"spotify:album:([A-Za-z0-9]+)", value)
    if album_uri_match:
        return "album", album_uri_match.group(1)

    raise HTTPException(
        status_code=400,
        detail="Paste a valid Spotify playlist or album link.",
    )


def refresh_spotify_access_token(db: Session, account: SpotifyAccount) -> str:
    refresh_token = getattr(account, "refresh_token", None)

    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Spotify access token expired and no refresh token is stored. Reconnect this account.",
        )

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Spotify client credentials are missing.",
        )

    basic_token = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("utf-8")

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={
            "Authorization": f"Basic {basic_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=20,
    )

    if not response.ok:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to refresh Spotify access token: {response.text}",
        )

    payload = response.json()
    access_token = payload.get("access_token")

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Spotify token refresh did not return an access token.",
        )

    account.access_token = access_token

    new_refresh_token = payload.get("refresh_token")
    if new_refresh_token:
        account.refresh_token = new_refresh_token

    db.add(account)
    db.commit()
    db.refresh(account)

    return access_token


def spotify_get(
    db: Session,
    account: SpotifyAccount,
    url: str,
    params: dict[str, Any] | None = None,
) -> dict:
    access_token = getattr(account, "access_token", None)

    if not access_token:
        access_token = refresh_spotify_access_token(db, account)

    tried_refresh = False

    while True:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=20,
        )

        if response.status_code == 401 and not tried_refresh:
            access_token = refresh_spotify_access_token(db, account)
            tried_refresh = True
            continue

        if response.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Spotify access token expired or invalid. Reconnect this account.",
            )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify import failed: {response.text}",
            )

        return response.json()


def normalize_track(track: dict, index: int) -> dict:
    artists = track.get("artists") or []
    artist_name = ", ".join(
        [artist.get("name") for artist in artists if artist.get("name")]
    )

    return {
        "id": track.get("id") or f"track-{index}",
        "title": track.get("name") or "Untitled Track",
        "artist": artist_name or "Unknown Artist",
    }


def fetch_playlist_tracks(db: Session, account: SpotifyAccount, spotify_id: str) -> dict:
    playlist = spotify_get(
        db,
        account,
        f"https://api.spotify.com/v1/playlists/{spotify_id}",
        params={
            "fields": "id,name,tracks(total,items(track(id,name,artists(name))),next)"
        },
    )

    display_name = playlist.get("name") or "Imported Playlist"
    tracks: list[dict] = []

    items = ((playlist.get("tracks") or {}).get("items")) or []
    for item in items:
        track = item.get("track") or {}
        if track:
            tracks.append(normalize_track(track, len(tracks)))

    next_url = (playlist.get("tracks") or {}).get("next")

    while next_url:
        page = spotify_get(db, account, next_url)
        page_items = page.get("items") or []

        for item in page_items:
            track = item.get("track") or {}
            if track:
                tracks.append(normalize_track(track, len(tracks)))

        next_url = page.get("next")

    return {
        "link": f"https://open.spotify.com/playlist/{spotify_id}",
        "source_type": "playlist",
        "display_name": display_name,
        "track_count": len(tracks),
        "tracks": tracks,
    }


def fetch_album_tracks(db: Session, account: SpotifyAccount, spotify_id: str) -> dict:
    album = spotify_get(
        db,
        account,
        f"https://api.spotify.com/v1/albums/{spotify_id}",
        params={
            "fields": "id,name,tracks(total,items(id,name,artists(name)),next)"
        },
    )

    display_name = album.get("name") or "Imported Album"
    tracks: list[dict] = []

    tracks_payload = album.get("tracks") or {}
    items = tracks_payload.get("items") or []

    for track in items:
        tracks.append(normalize_track(track, len(tracks)))

    next_url = tracks_payload.get("next")

    while next_url:
        page = spotify_get(db, account, next_url)
        page_items = page.get("items") or []

        for track in page_items:
            tracks.append(normalize_track(track, len(tracks)))

        next_url = page.get("next")

    return {
        "link": f"https://open.spotify.com/album/{spotify_id}",
        "source_type": "album",
        "display_name": display_name,
        "track_count": len(tracks),
        "tracks": tracks,
    }


@router.post("/accounts/{account_id}/curation/import")
def import_spotify_link(
    account_id: int,
    payload: ImportSpotifyLinkRequest,
    db: Session = Depends(get_db),
):
    account = get_account_or_404(db, account_id)
    resource_type, spotify_id = extract_spotify_resource(payload.link)

    if resource_type == "playlist":
        return fetch_playlist_tracks(db, account, spotify_id)

    if resource_type == "album":
        return fetch_album_tracks(db, account, spotify_id)

    raise HTTPException(status_code=400, detail="Unsupported Spotify link.")