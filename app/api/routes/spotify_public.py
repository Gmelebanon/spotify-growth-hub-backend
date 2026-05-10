from fastapi import APIRouter, HTTPException
import requests
import os

router = APIRouter()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")


def get_spotify_token():
    response = requests.post(
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

    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"

    results = []

    while url:
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
    raise HTTPException(
        status_code=response.status_code,
        detail={
            "message": "Spotify playlist fetch failed",
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
        "tracks": results
    }
