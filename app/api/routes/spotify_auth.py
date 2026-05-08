import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.spotify_account import SpotifyAccount

load_dotenv()

router = APIRouter(prefix="/api/spotify", tags=["spotify-auth"])

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv(
    "SPOTIFY_REDIRECT_URI",
    "https://spotify-growth-hub-backend.onrender.com/api/spotify/callback",
)
FRONTEND_SUCCESS_URL = os.getenv(
    "FRONTEND_SUCCESS_URL",
    "https://nerd-engine.vercel.app/dashboard",
)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PROFILE_URL = "https://api.spotify.com/v1/me"

SCOPES = " ".join(
    [
        "user-read-email",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
        "ugc-image-upload",
    ]
)


def update_account_tokens(
    account: SpotifyAccount,
    spotify_user_id: str,
    display_name: str,
    email: str | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime,
):
    account.spotify_user_id = spotify_user_id
    account.display_name = display_name
    account.email = email
    account.access_token = access_token

    if refresh_token:
        account.refresh_token = refresh_token

    account.token_expires_at = expires_at

    if hasattr(account, "updated_at"):
        account.updated_at = datetime.utcnow()


@router.get("/login")
def spotify_login():
    if not SPOTIFY_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID environment variable",
        )

    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": SCOPES,
        "show_dialog": "true",
    }

    return RedirectResponse(f"{SPOTIFY_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
def spotify_callback(
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify authorization failed: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing Spotify authorization code")

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET environment variable",
        )

    token_response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
        timeout=30,
    )

    if not token_response.ok:
        raise HTTPException(
            status_code=token_response.status_code,
            detail=f"Failed to get Spotify token: {token_response.text}",
        )

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in") or 3600)

    if not access_token:
        raise HTTPException(status_code=400, detail="Spotify did not return access token")

    profile_response = requests.get(
        SPOTIFY_PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )

    if not profile_response.ok:
        raise HTTPException(
            status_code=profile_response.status_code,
            detail=f"Failed to fetch Spotify profile: {profile_response.text}",
        )

    profile = profile_response.json()
    spotify_user_id = profile.get("id")

    if not spotify_user_id:
        raise HTTPException(status_code=400, detail="Spotify profile did not return user id")

    display_name = profile.get("display_name") or spotify_user_id
    email = profile.get("email")
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    # First match by Spotify user id.
    account = (
        db.query(SpotifyAccount)
        .filter(SpotifyAccount.spotify_user_id == spotify_user_id)
        .first()
    )

    # Fallback: some old/manual rows may already exist by display name or email.
    # This avoids duplicate-key errors and updates the existing row with tokens.
    if not account and email:
        account = db.query(SpotifyAccount).filter(SpotifyAccount.email == email).first()

    if not account and display_name:
        account = (
            db.query(SpotifyAccount)
            .filter(SpotifyAccount.display_name == display_name)
            .first()
        )

    if account:
        update_account_tokens(
            account=account,
            spotify_user_id=spotify_user_id,
            display_name=display_name,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
    else:
        account = SpotifyAccount(
            spotify_user_id=spotify_user_id,
            display_name=display_name,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
        )
        db.add(account)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        # Last safety fallback for duplicate rows/race conditions.
        account = (
            db.query(SpotifyAccount)
            .filter(SpotifyAccount.spotify_user_id == spotify_user_id)
            .first()
        )

        if not account and email:
            account = db.query(SpotifyAccount).filter(SpotifyAccount.email == email).first()

        if not account and display_name:
            account = (
                db.query(SpotifyAccount)
                .filter(SpotifyAccount.display_name == display_name)
                .first()
            )

        if not account:
            raise HTTPException(
                status_code=500,
                detail="Could not save Spotify account because of a database duplicate conflict.",
            )

        update_account_tokens(
            account=account,
            spotify_user_id=spotify_user_id,
            display_name=display_name,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

        db.add(account)
        db.commit()

    db.refresh(account)

    return RedirectResponse(
        f"{FRONTEND_SUCCESS_URL}?spotify_connected=1&account_id={account.id}"
    )


@router.get("/debug")
def spotify_auth_debug():
    return {
        "success": True,
        "client_id_configured": bool(SPOTIFY_CLIENT_ID),
        "client_secret_configured": bool(SPOTIFY_CLIENT_SECRET),
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "frontend_success_url": FRONTEND_SUCCESS_URL,
    }
