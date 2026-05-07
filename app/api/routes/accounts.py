import base64
import os
import urllib.parse
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.spotify_account import SpotifyAccount

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

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(url=f"{frontend_url}/dashboard", status_code=302)


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