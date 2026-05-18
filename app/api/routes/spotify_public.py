from fastapi import APIRouter, HTTPException
import os
import random
import time

import requests

router = APIRouter()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

SPOTIFY_PUBLIC_REQUEST_DELAY_SECONDS = float(os.getenv("SPOTIFY_PUBLIC_REQUEST_DELAY_SECONDS", "0.35"))
SPOTIFY_PUBLIC_MAX_RETRIES = int(os.getenv("SPOTIFY_PUBLIC_MAX_RETRIES", "4"))


def sleep_between_public_spotify_requests():
    if SPOTIFY_PUBLIC_REQUEST_DELAY_SECONDS <= 0:
        return

    lower = max(0, SPOTIFY_PUBLIC_REQUEST_DELAY_SECONDS * 0.7)
    upper = max(lower, SPOTIFY_PUBLIC_REQUEST_DELAY_SECONDS * 1.3)
    time.sleep(random.uniform(lower, upper))


def parse_retry_after_seconds(value: str | None, fallback: int = 5) -> int:
    if not value:
        return fallback

    try:
        return max(1, int(float(value)))
    except Exception:
        return fallback


def spotify_public_request(method: str, url: str, **kwargs):
    last_response = None

    for attempt in range(max(1, SPOTIFY_PUBLIC_MAX_RETRIES)):
        sleep_between_public_spotify_requests()
        response = requests.request(method, url, **kwargs)
        last_response = response

        if response.status_code != 429:
            return response

        wait_seconds = parse_retry_after_seconds(response.headers.get("Retry-After")) + 1
        print(f"Spotify public rate limited attempt={attempt + 1}/{SPOTIFY_PUBLIC_MAX_RETRIES}; waiting {wait_seconds}s")
        time.sleep(wait_seconds)

    return last_response


def get_spotify_token():
    response = spotify_public_request(
        "POST",
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Spotify token failed",
                "spotify_status": response.status_code,
                "spotify_response": response.text,
            },
        )

    return response.json()["access_token"]


@router.get("/api/spotify/public-playlist/{playlist_id}/tracks")
async def get_public_playlist_tracks(playlist_id: str):
    token = get_spotify_token()

    headers = {
        "Authorization": f"Bearer {token}"
    }

    playlist_response = spotify_public_request(
        "GET",
        f"https://api.spotify.com/v1/playlists/{playlist_id}",
        headers=headers,
    )

    if playlist_response.status_code != 200:
        raise HTTPException(
            status_code=playlist_response.status_code,
            detail={
                "message": "Spotify playlist fetch failed",
                "spotify_status": playlist_response.status_code,
                "spotify_response": playlist_response.text,
                "playlist_id": playlist_id,
            },
        )

    playlist_data = playlist_response.json()

    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"

    results = []

    while url:
        response = spotify_public_request("GET", url, headers=headers)

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail={
                    "message": "Spotify playlist tracks fetch failed",
                    "spotify_status": response.status_code,
                    "spotify_response": response.text,
                    "playlist_id": playlist_id,
                },
            )

        data = response.json()

        for item in data.get("items", []):
            track = item.get("track")

            if not track:
                continue

            artists = ", ".join(
                artist["name"]
                for artist in track.get("artists", [])
            )

            image_url = None

            album = track.get("album")

            if album and album.get("images"):
                image_url = album["images"][0]["url"]

            results.append({
                "spotify_id": track.get("id"),
                "title": track.get("name"),
                "artist": artists,
                "image_url": image_url,
                "preview_url": track.get("preview_url"),
            })

        url = data.get("next")

    return {
        "playlist": {
            "id": playlist_id,
            "name": playlist_data.get("name") or "Imported Spotify Playlist",
            "owner_name": playlist_data.get("owner", {}).get("display_name") or "Spotify",
        },
        "tracks": results,
    }
