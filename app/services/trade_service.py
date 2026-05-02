from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.trade import Trade, TradePlacement
from app.services.spotify_playlist_service import (
    get_account_or_404,
    _spotify_get_with_auto_refresh,
)

SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists"
SPOTIFY_TRACK_URL = "https://api.spotify.com/v1/tracks"


def serialize_trade(trade: Trade) -> dict:
    return {
        "id": trade.id,
        "account_id": trade.account_id,
        "track_name": trade.track_name,
        "artist_name": trade.artist_name,
        "playlist_count": trade.playlist_count,
        "status": trade.status,
        "created_at": trade.created_at.isoformat(),
        "expires_at": trade.expires_at.isoformat(),
        "placements": [
            {
                "id": placement.id,
                "playlist_name": placement.playlist_name,
                "note": placement.note,
            }
            for placement in trade.placements
        ],
    }


def extract_spotify_id_from_url(url: str, resource_type: str) -> str:
    try:
        parsed = urlparse(url.strip())
        path_parts = [part for part in parsed.path.split("/") if part]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL.")

    if resource_type not in path_parts:
        raise HTTPException(
            status_code=400,
            detail=f"URL is not a valid Spotify {resource_type} link.",
        )

    index = path_parts.index(resource_type)
    if index + 1 >= len(path_parts):
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract Spotify {resource_type} ID from URL.",
        )

    return path_parts[index + 1]


def list_trades_for_account(db: Session, account_id: int) -> dict:
    trades = (
        db.query(Trade)
        .filter(Trade.account_id == account_id)
        .order_by(Trade.created_at.desc())
        .all()
    )

    return {
        "active": [serialize_trade(t) for t in trades if t.status == "active"],
        "past": [serialize_trade(t) for t in trades if t.status == "past"],
    }


def create_trade_for_account(
    db: Session,
    account_id: int,
    track_name: str,
    artist_name: str,
    playlist_count: int,
    placements: list[str],
    status: str = "active",
) -> dict:
    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(days=28)

    trade = Trade(
        account_id=account_id,
        track_name=track_name,
        artist_name=artist_name,
        playlist_count=playlist_count,
        status=status,
        created_at=created_at,
        expires_at=expires_at,
    )

    db.add(trade)
    db.flush()

    for playlist_name in placements:
        name = playlist_name.strip()
        if not name:
            continue
        db.add(
            TradePlacement(
                trade_id=trade.id,
                playlist_name=name,
            )
        )

    db.commit()
    db.refresh(trade)

    return serialize_trade(trade)


def extend_trade(db: Session, trade_id: int) -> dict | None:
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if trade is None:
        return None

    trade.expires_at = trade.expires_at + timedelta(days=28)
    db.commit()
    db.refresh(trade)
    return serialize_trade(trade)


def archive_trade(db: Session, trade_id: int) -> dict | None:
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if trade is None:
        return None

    trade.status = "past"
    db.commit()
    db.refresh(trade)
    return serialize_trade(trade)


def delete_trade(db: Session, trade_id: int) -> bool:
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if trade is None:
        return False

    db.delete(trade)
    db.commit()
    return True


def scan_trade(db: Session, trade_id: int) -> dict | None:
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if trade is None:
        return None

    next_index = len(trade.placements) + 1
    db.add(
        TradePlacement(
            trade_id=trade.id,
            playlist_name=f"Auto Found Playlist {next_index}",
        )
    )
    trade.playlist_count += 1

    db.commit()
    db.refresh(trade)
    return serialize_trade(trade)


def analyze_playlist_for_account(
    db: Session,
    account_id: int,
    playlist_url: str,
) -> dict:
    account = get_account_or_404(db, account_id)
    playlist_id = extract_spotify_id_from_url(playlist_url, "playlist")

    playlist_response = _spotify_get_with_auto_refresh(
        db=db,
        account=account,
        url=f"{SPOTIFY_PLAYLIST_URL}/{playlist_id}",
    )

    if playlist_response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify playlist analyze failed ({playlist_response.status_code}): {playlist_response.text}",
        )

    playlist_data = playlist_response.json()

    placements = []
    limit = 100
    offset = 0
    total = 0

    while True:
        tracks_response = _spotify_get_with_auto_refresh(
            db=db,
            account=account,
            url=f"{SPOTIFY_PLAYLIST_URL}/{playlist_id}/tracks",
            params={"limit": limit, "offset": offset},
        )

        if tracks_response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify playlist tracks fetch failed ({tracks_response.status_code}): {tracks_response.text}",
            )

        tracks_data = tracks_response.json()
        total = tracks_data.get("total", total)
        items = tracks_data.get("items") or []

        for item in items:
            track = item.get("track") or {}
            track_name = track.get("name")
            artists = track.get("artists") or []
            artist_names = ", ".join(
                artist.get("name") for artist in artists if artist.get("name")
            )

            if track_name:
                placements.append({
                    "track_name": track_name,
                    "artist_name": artist_names or "Unknown Artist",
                    "display_name": f"{track_name} — {artist_names or 'Unknown Artist'}",
                })

        if len(items) < limit:
            break

        offset += limit

    owner = playlist_data.get("owner") or {}
    images = playlist_data.get("images") or []

    return {
        "playlist_id": playlist_data.get("id"),
        "name": playlist_data.get("name"),
        "description": playlist_data.get("description"),
        "owner_display_name": owner.get("display_name"),
        "tracks_total": total or (playlist_data.get("tracks") or {}).get("total", 0),
        "image_url": images[0].get("url") if images else None,
        "placements": placements,
    }


def lookup_tracks_for_account(
    db: Session,
    account_id: int,
    track_urls: list[str],
) -> dict:
    account = get_account_or_404(db, account_id)

    results = []

    for url in track_urls:
        if not url.strip():
            continue

        track_id = extract_spotify_id_from_url(url, "track")

        response = _spotify_get_with_auto_refresh(
            db=db,
            account=account,
            url=f"{SPOTIFY_TRACK_URL}/{track_id}",
        )

        if response.status_code != 200:
            continue

        data = response.json()
        album = data.get("album") or {}
        artists = data.get("artists") or []

        results.append(
            {
                "id": data.get("id"),
                "url": url,
                "title": data.get("name"),
                "artist": ", ".join(
                    artist.get("name") for artist in artists if artist.get("name")
                )
                or "Unknown Artist",
                "album": album.get("name"),
                "image_url": (album.get("images") or [{}])[0].get("url")
                if album.get("images")
                else None,
            }
        )

    return {
        "count": len(results),
        "results": results,
    }