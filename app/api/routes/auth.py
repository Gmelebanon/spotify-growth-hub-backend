import os
from datetime import datetime
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.spotify_account import SpotifyAccount

router = APIRouter(prefix="/api", tags=["auth", "accounts"])

SPOTIFY_SCOPES = [
    "user-read-email",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
]


class CreateAccountRequest(BaseModel):
    display_name: str | None = None
    spotify_user_id: str | None = None
    email: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


def get_spotify_env():
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, or SPOTIFY_REDIRECT_URI in backend .env",
        )

    return client_id, client_secret, redirect_uri


@router.get("/auth/login")
def spotify_login():
    client_id, _, redirect_uri = get_spotify_env()

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SPOTIFY_SCOPES),
        "show_dialog": "true",
    }

    return RedirectResponse(
        f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    )


@router.get("/auth/callback")
def spotify_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        raise HTTPException(status_code=400, detail=error)

    if not code:
        raise HTTPException(status_code=400, detail="Missing Spotify auth code")

    client_id, client_secret, redirect_uri = get_spotify_env()

    token_response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=20,
    )

    if not token_response.ok:
        raise HTTPException(
            status_code=400,
            detail=f"Spotify token exchange failed: {token_response.text}",
        )

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="Spotify did not return access token")

    profile_response = requests.get(
        "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )

    if not profile_response.ok:
        raise HTTPException(
            status_code=400,
            detail=f"Spotify profile fetch failed: {profile_response.text}",
        )

    profile = profile_response.json()
    spotify_user_id = profile.get("id")
    display_name = profile.get("display_name") or spotify_user_id or "Spotify Account"
    email = profile.get("email")

    if not spotify_user_id:
        raise HTTPException(status_code=400, detail="Spotify profile missing user ID")

    account = (
        db.query(SpotifyAccount)
        .filter(SpotifyAccount.spotify_user_id == spotify_user_id)
        .first()
    )

    if account:
        account.display_name = display_name
        account.email = email
        account.access_token = access_token
        if refresh_token:
            account.refresh_token = refresh_token
    else:
        account = SpotifyAccount(
            display_name=display_name,
            spotify_user_id=spotify_user_id,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            created_at=datetime.utcnow(),
        )

    db.add(account)
    db.commit()
    db.refresh(account)

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(f"{frontend_url}/dashboard")


@router.get("/accounts")
def get_accounts(db: Session = Depends(get_db)):
    accounts = db.query(SpotifyAccount).order_by(SpotifyAccount.id.asc()).all()

    return {
        "items": [
            {
                "id": account.id,
                "display_name": account.display_name,
                "spotify_user_id": account.spotify_user_id,
                "email": account.email,
            }
            for account in accounts
        ]
    }


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
        "created_at": account.created_at.isoformat() if account.created_at else None,
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