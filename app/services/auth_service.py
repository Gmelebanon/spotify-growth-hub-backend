from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.spotify_account import SpotifyAccount
from app.schemas.auth import (
    SpotifyAccountItem,
    AuthCallbackResponse,
    AccountStatusResponse,
    RefreshResponse,
)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"

SPOTIFY_SCOPES = " ".join(
    [
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-public",
        "playlist-modify-private",
        "user-read-private",
        "user-read-email",
    ]
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _compute_expires_at(expires_in: int) -> datetime:
    return _now_utc() + timedelta(seconds=max(expires_in - 60, 0))


def _is_expired(expires_at: datetime) -> bool:
    return expires_at <= _now_utc()


def _to_account_item(account: SpotifyAccount) -> SpotifyAccountItem:
    return SpotifyAccountItem(
        id=account.id,
        spotify_user_id=account.spotify_user_id,
        display_name=account.display_name,
        expires_at=account.expires_at,
        token_expired=_is_expired(account.expires_at),
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def build_spotify_auth_url() -> str:
    params = {
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
        "scope": SPOTIFY_SCOPES,
        "show_dialog": "true",
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"


def _exchange_code(code: str) -> dict:
    try:
        response = httpx.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
            },
            auth=(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET),
            timeout=10,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Spotify token endpoint: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify token exchange failed ({response.status_code}): {response.text}",
        )

    return response.json()


def _fetch_spotify_profile(access_token: str) -> dict:
    try:
        response = httpx.get(
            SPOTIFY_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Spotify profile endpoint: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify profile fetch failed ({response.status_code}): {response.text}",
        )

    data = response.json()

    if "id" not in data or not data["id"]:
        raise HTTPException(
            status_code=502,
            detail="Spotify profile response did not include a user id.",
        )

    return data


def _call_spotify_refresh(refresh_token: str) -> dict:
    try:
        response = httpx.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET),
            timeout=10,
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

    return response.json()


def _get_account_by_id(db: Session, account_id: int) -> SpotifyAccount | None:
    return db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()


def _upsert_account(
    db: Session,
    spotify_user_id: str,
    display_name: str | None,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> SpotifyAccount:
    account = (
        db.query(SpotifyAccount)
        .filter(SpotifyAccount.spotify_user_id == spotify_user_id)
        .first()
    )

    now = _now_utc()

    if account is None:
        account = SpotifyAccount(
            spotify_user_id=spotify_user_id,
            display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        db.add(account)
    else:
        account.display_name = display_name
        account.access_token = access_token
        account.refresh_token = refresh_token
        account.expires_at = expires_at
        account.updated_at = now

    db.commit()
    db.refresh(account)
    return account


def get_all_accounts(db: Session) -> list[SpotifyAccountItem]:
    accounts = db.query(SpotifyAccount).order_by(SpotifyAccount.created_at.desc()).all()
    return [_to_account_item(a) for a in accounts]


def get_account_status(db: Session, account_id: int) -> AccountStatusResponse:
    account = _get_account_by_id(db, account_id)

    if account is None:
        raise HTTPException(
            status_code=404,
            detail=f"No Spotify account found with id={account_id}.",
        )

    return AccountStatusResponse(
        connected=True,
        account_id=account.id,
        spotify_user_id=account.spotify_user_id,
        display_name=account.display_name,
        expires_at=account.expires_at,
        token_expired=_is_expired(account.expires_at),
    )


def handle_callback(db: Session, code: str) -> AuthCallbackResponse:
    token_data = _exchange_code(code)

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token or not refresh_token:
        raise HTTPException(
            status_code=502,
            detail="Spotify token response is missing access_token or refresh_token.",
        )

    profile = _fetch_spotify_profile(access_token)

    spotify_user_id = profile["id"]
    display_name = profile.get("display_name")
    expires_at = _compute_expires_at(expires_in)

    account = _upsert_account(
        db=db,
        spotify_user_id=spotify_user_id,
        display_name=display_name,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )

    return AuthCallbackResponse(
        message="Spotify account connected successfully.",
        account_id=account.id,
        spotify_user_id=account.spotify_user_id,
        display_name=account.display_name,
        expires_at=account.expires_at,
    )


def refresh_account_token(db: Session, account_id: int) -> RefreshResponse:
    account = _get_account_by_id(db, account_id)

    if account is None:
        raise HTTPException(
            status_code=404,
            detail=f"No Spotify account found with id={account_id}.",
        )

    token_data = _call_spotify_refresh(account.refresh_token)

    new_access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in", 3600)

    if not new_access_token:
        raise HTTPException(
            status_code=502,
            detail="Spotify refresh response is missing access_token.",
        )

    new_refresh_token = token_data.get("refresh_token") or account.refresh_token
    expires_at = _compute_expires_at(expires_in)

    account.access_token = new_access_token
    account.refresh_token = new_refresh_token
    account.expires_at = expires_at
    account.updated_at = _now_utc()

    db.commit()
    db.refresh(account)

    return RefreshResponse(
        message="Access token refreshed successfully.",
        account_id=account.id,
        spotify_user_id=account.spotify_user_id,
        display_name=account.display_name,
        expires_at=account.expires_at,
        token_expired=_is_expired(account.expires_at),
    )