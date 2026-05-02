from datetime import datetime
import base64
import os
import urllib.parse

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.follower_history import FollowerHistory
from app.models.spotify_account import SpotifyAccount
from app.models.playlist import Playlist

router = APIRouter(prefix="/api", tags=["auth", "accounts"])


class CreateAccountRequest(BaseModel):
    display_name: str | None = None
    spotify_user_id: str | None = None
    email: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


def _get_spotify_env():
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(status_code=500, detail="Missing Spotify env vars")

    return client_id, client_secret, redirect_uri


def _refresh_spotify_access_token(db: Session, account: SpotifyAccount) -> str:
    refresh_token = getattr(account, "refresh_token", None)
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Spotify access token expired and no refresh token is stored. Reconnect the Spotify account.",
        )

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Spotify client credentials are missing in the backend environment.",
        )

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )

    if not response.ok:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to refresh Spotify access token: {response.text}",
        )

    payload = response.json()
    new_access_token = payload.get("access_token")
    if not new_access_token:
        raise HTTPException(
            status_code=401,
            detail="Spotify token refresh did not return a new access token.",
        )

    account.access_token = new_access_token

    new_refresh_token = payload.get("refresh_token")
    if new_refresh_token:
        account.refresh_token = new_refresh_token

    if hasattr(account, "updated_at"):
        account.updated_at = datetime.utcnow()

    db.add(account)
    db.commit()
    db.refresh(account)

    return new_access_token


import time
import random


SPOTIFY_SAFE_MODE = True
SPOTIFY_MIN_DELAY = 4.0
SPOTIFY_MAX_DELAY = 7.0


def _spotify_safe_sleep():
    if SPOTIFY_SAFE_MODE:
        time.sleep(random.uniform(SPOTIFY_MIN_DELAY, SPOTIFY_MAX_DELAY))


def _spotify_get(
    db: Session,
    account: SpotifyAccount,
    url: str,
    params: dict | None = None,
) -> dict:
    access_token = getattr(account, "access_token", None)
    if not access_token:
        access_token = _refresh_spotify_access_token(db, account)

    tried_refresh = False

    while True:
        _spotify_safe_sleep()

        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=60,
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "30"))
            time.sleep(retry_after + 5)
            continue

        if response.status_code == 401 and not tried_refresh:
            access_token = _refresh_spotify_access_token(db, account)
            tried_refresh = True
            continue

        if response.status_code in [401, 403]:
            raise HTTPException(
                status_code=response.status_code,
                detail="Spotify access denied. Reconnect account or check permissions.",
            )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify fetch failed: {response.text}",
            )

        return response.json()


def _spotify_get_me_playlists(
    db: Session,
    account: SpotifyAccount,
    limit: int,
    offset: int,
) -> dict:
    return _spotify_get(
        db=db,
        account=account,
        url="https://api.spotify.com/v1/me/playlists",
        params={"limit": limit, "offset": offset},
    )


def _spotify_get_playlist_detail(
    db: Session,
    account: SpotifyAccount,
    spotify_playlist_id: str,
) -> dict:
    return _spotify_get(
        db=db,
        account=account,
        url=f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}",
        params={
            "fields": "id,name,description,images,external_urls,followers(total),tracks(total),owner(display_name),public"
        },
    )


def _resolve_followers_value(detail_followers: int | None, existing_playlist: Playlist | None) -> int:
    incoming = detail_followers if isinstance(detail_followers, int) else None
    existing = getattr(existing_playlist, "followers", 0) or 0 if existing_playlist else 0

    if incoming is None:
        return existing

    if incoming > 0:
        return incoming

    if existing > 0:
        return existing

    return 0


def _upsert_playlist(db: Session, account_id: int, sp: dict) -> tuple[str, Playlist | None]:
    spotify_id = sp.get("id")
    if not spotify_id:
        return "skipped", None

    playlist = (
    db.query(Playlist)
    .filter(
        Playlist.account_id == account_id,
        Playlist.spotify_playlist_id == spotify_id,
    )
    .first()
)

    images = sp.get("images") or []
    owner = sp.get("owner") or {}
    tracks = sp.get("tracks") or {}
    external_urls = sp.get("external_urls") or {}
    followers = sp.get("followers") or {}

    resolved_followers = _resolve_followers_value(
        followers.get("total"),
        playlist,
    )

    payload = {
        "account_id": account_id,
        "name": sp.get("name") or "Untitled Playlist",
        "description": sp.get("description"),
        "spotify_id": spotify_id,
        "spotify_playlist_id": spotify_id,
        "image_url": images[0].get("url") if images else None,
        "spotify_url": external_urls.get("spotify"),
        "external_url": external_urls.get("spotify"),
        "url": external_urls.get("spotify"),
        "playlist_url": external_urls.get("spotify"),
        "external_id": spotify_id,
        "followers": resolved_followers,
        "tracks_total": tracks.get("total") or 0,
        "tracks_count": tracks.get("total") or 0,
        "owner_name": owner.get("display_name"),
        "owner_display_name": owner.get("display_name"),
        "public": sp.get("public"),
    }

    if playlist:
        for key, value in payload.items():
            setattr(playlist, key, value)
        if hasattr(playlist, "updated_at"):
            playlist.updated_at = datetime.utcnow()
        return "updated", playlist

    playlist = Playlist(**payload)
    db.add(playlist)
    db.flush()
    return "created", playlist


def _create_follower_history_snapshot(db: Session, playlist: Playlist):
    followers_value = getattr(playlist, "followers", 0) or 0
    if followers_value <= 0:
        return

    history_entry = FollowerHistory(
        playlist_id=playlist.id,
        followers=followers_value,
        created_at=datetime.utcnow(),
    )
    db.add(history_entry)


@router.get("/auth/login")
def spotify_login():
    client_id, _, redirect_uri = _get_spotify_env()

    scope = (
    "user-read-email "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-public "
    "playlist-modify-private"
)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
    }

    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/auth/callback")
def spotify_callback(code: str | None = None, db: Session = Depends(get_db)):
    if not code:
        raise HTTPException(status_code=400, detail="Missing Spotify auth code")

    client_id, client_secret, redirect_uri = _get_spotify_env()

    basic_token = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("utf-8")

    token_response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={
            "Authorization": f"Basic {basic_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=20,
    )

    if not token_response.ok:
        raise HTTPException(status_code=400, detail=token_response.text)

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="Spotify did not return an access token")

    profile_response = requests.get(
        "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )

    if not profile_response.ok:
        raise HTTPException(status_code=400, detail=profile_response.text)

    profile = profile_response.json()
    spotify_user_id = profile.get("id")

    if not spotify_user_id:
        raise HTTPException(status_code=400, detail="Spotify profile is missing user id")

    account = (
        db.query(SpotifyAccount)
        .filter(SpotifyAccount.spotify_user_id == spotify_user_id)
        .first()
    )

    now = datetime.utcnow()

    if account:
        account.display_name = profile.get("display_name")
        account.email = profile.get("email")
        account.access_token = access_token
        if refresh_token:
            account.refresh_token = refresh_token
        if hasattr(account, "updated_at"):
            account.updated_at = now
    else:
        account = SpotifyAccount(
            display_name=profile.get("display_name"),
            spotify_user_id=spotify_user_id,
            email=profile.get("email"),
            access_token=access_token,
            refresh_token=refresh_token,
            created_at=now,
        )
        if hasattr(account, "updated_at"):
            account.updated_at = now
        db.add(account)

    db.commit()
    db.refresh(account)

    return RedirectResponse(url="http://localhost:3000/dashboard", status_code=302)


@router.get("/accounts")
def get_accounts(db: Session = Depends(get_db)):
    accounts = db.query(SpotifyAccount).order_by(SpotifyAccount.id.asc()).all()

    return [
        {
            "id": account.id,
            "display_name": account.display_name,
            "spotify_user_id": account.spotify_user_id,
            "email": account.email,
            "created_at": account.created_at.isoformat()
            if getattr(account, "created_at", None)
            else None,
            "updated_at": account.updated_at.isoformat()
            if hasattr(account, "updated_at") and account.updated_at
            else None,
        }
        for account in accounts
    ]


@router.get("/accounts/{account_id}")
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    return {
        "id": account.id,
        "display_name": account.display_name,
        "spotify_user_id": account.spotify_user_id,
        "email": account.email,
        "created_at": account.created_at.isoformat()
        if getattr(account, "created_at", None)
        else None,
        "updated_at": account.updated_at.isoformat()
        if hasattr(account, "updated_at") and account.updated_at
        else None,
    }


@router.post("/accounts")
def create_account(payload: CreateAccountRequest, db: Session = Depends(get_db)):
    account = SpotifyAccount(
        display_name=payload.display_name,
        spotify_user_id=payload.spotify_user_id,
        email=payload.email,
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        created_at=datetime.utcnow(),
    )

    db.add(account)
    db.commit()
    db.refresh(account)

    return {
        "id": account.id,
        "display_name": account.display_name,
        "spotify_user_id": account.spotify_user_id,
        "email": account.email,
    }


@router.post("/accounts/{account_id}/playlists/sync")
def sync_account_playlists(
    account_id: int,
    db: Session = Depends(get_db),
):
    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    created = 0
    updated = 0
    skipped = 0
    fetched = 0
    pages = 0

    url = "https://api.spotify.com/v1/me/playlists"

    try:
        while url:
            payload = _spotify_get(
                db=db,
                account=account,
                url=url,
                params={"limit": 50},
            )

            items = payload.get("items") or []
            pages += 1

            for item in items:
                spotify_id = item.get("id")

                if not spotify_id:
                    skipped += 1
                    continue

                playlist = (
                    db.query(Playlist)
                    .filter(
                        Playlist.account_id == account_id,
                        Playlist.spotify_playlist_id == spotify_id,
                    )
                    .first()
                )

                images = item.get("images") or []
                tracks = item.get("tracks") or {}
                external_urls = item.get("external_urls") or {}
                owner = item.get("owner") or {}

                payload_data = {
                    "account_id": account_id,
                    "name": item.get("name") or "Untitled Playlist",
                    "description": item.get("description"),
                    "spotify_id": spotify_id,
                    "spotify_playlist_id": spotify_id,
                    "image_url": images[0].get("url") if images else None,
                    "spotify_url": external_urls.get("spotify"),
                    "external_url": external_urls.get("spotify"),
                    "url": external_urls.get("spotify"),
                    "playlist_url": external_urls.get("spotify"),
                    "external_id": spotify_id,
                    "tracks_total": tracks.get("total") or 0,
                    "tracks_count": tracks.get("total") or 0,
                    "owner_name": owner.get("display_name"),
                    "owner_display_name": owner.get("display_name"),
                    "public": item.get("public"),
                }

                if playlist:
                    for key, value in payload_data.items():
                        setattr(playlist, key, value)

                    if hasattr(playlist, "updated_at"):
                        playlist.updated_at = datetime.utcnow()

                    updated += 1
                else:
                    playlist = Playlist(
                        **payload_data,
                        followers=0,
                        created_at=datetime.utcnow(),
                    )

                    if hasattr(playlist, "updated_at"):
                        playlist.updated_at = datetime.utcnow()

                    db.add(playlist)
                    created += 1

                fetched += 1

            db.commit()
            url = payload.get("next")

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Playlist sync failed for account {account_id}: {str(exc)}",
        ) from exc

    return {
        "account_id": account_id,
        "message": "Playlists synced fast",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "fetched": fetched,
        "total": created + updated,
        "pages": pages,
    }