
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": SCOPES,import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
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

SCOPES = (
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-private "
    "playlist-modify-public "
    "ugc-image-upload"
)


        "show_dialog": "true",
    }

    auth_url = "https://accounts.spotify.com/authorize?" + urlencode(params)

    return RedirectResponse(auth_url)


@router.get("/callback")
def spotify_callback(code: str, db: Session = Depends(get_db)):
    token_response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
    )

    if token_response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to get Spotify token")

    token_data = token_response.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    me_response = requests.get(
        "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if me_response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Spotify profile")

    profile = me_response.json()

    spotify_user_id = profile.get("id")
    display_name = profile.get("display_name")
    email = profile.get("email")

    existing = (
        db.query(SpotifyAccount)
        .filter(SpotifyAccount.spotify_user_id == spotify_user_id)
        .first()
    )

    expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    if existing:
        existing.display_name = display_name
        existing.email = email
        existing.access_token = access_token
        existing.refresh_token = refresh_token
        existing.token_expires_at = expires_at
    else:
        existing = SpotifyAccount(
            spotify_user_id=spotify_user_id,
            display_name=display_name,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
        )

        db.add(existing)

    db.commit()

    return {
        "success": True,
        "message": "Spotify account connected successfully",
        "account": {
            "display_name": display_name,
            "spotify_user_id": spotify_user_id,
            "email": email,
        },
    }