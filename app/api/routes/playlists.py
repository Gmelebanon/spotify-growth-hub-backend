import os
import re
import random
import time
import threading
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.follower_history import FollowerHistory
from app.models.playlist import Playlist
from app.models.ads_meta import AdsMeta
from app.models.spotify_account import SpotifyAccount

router = APIRouter(tags=["playlists"])
logger = logging.getLogger(__name__)


SPOTIFY_REQUEST_DELAY_SECONDS = float(os.getenv("SPOTIFY_REQUEST_DELAY_SECONDS", "0.45"))
SPOTIFY_ACCOUNT_SYNC_COOLDOWN_SECONDS = float(os.getenv("SPOTIFY_ACCOUNT_SYNC_COOLDOWN_SECONDS", "5"))
SPOTIFY_MAX_RETRIES = int(os.getenv("SPOTIFY_MAX_RETRIES", "4"))
SPOTIFY_SUSPICIOUS_DAILY_VALUE_LIMIT = int(os.getenv("SPOTIFY_SUSPICIOUS_DAILY_VALUE_LIMIT", "100"))
SYNC_ALL_COOLDOWN_SECONDS = int(os.getenv("SYNC_ALL_COOLDOWN_SECONDS", "900"))

SYNC_ALL_LOCK = threading.RLock()
SYNC_ALL_STATE = {
    "in_progress": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
}


def sleep_between_spotify_requests():
    """Small jittered cooldown to avoid hammering Spotify during sync jobs."""
    if SPOTIFY_REQUEST_DELAY_SECONDS <= 0:
        return

    lower = max(0, SPOTIFY_REQUEST_DELAY_SECONDS * 0.7)
    upper = max(lower, SPOTIFY_REQUEST_DELAY_SECONDS * 1.3)
    time.sleep(random.uniform(lower, upper))


def parse_retry_after_seconds(value: str | None, fallback: int = 5) -> int:
    if not value:
        return fallback

    try:
        return max(1, int(float(value)))
    except Exception:
        return fallback


class UpdateAdsMetaRequest(BaseModel):
    category: str | None = None
    genre: str | None = None
    country: str | None = None
    master_playlist: str | None = None
    ads: list | None = None
    color: str | None = None


def serialize_ads_meta(meta: AdsMeta | None):
    if not meta:
        return {
            "category": None,
            "genre": None,
            "country": None,
            "master_playlist": None,
            "ads": [],
            "color": None,
        }

    return {
        "category": meta.category,
        "genre": meta.genre,
        "country": meta.country,
        "master_playlist": meta.master_playlist,
        "ads": meta.ads or [],
        "color": meta.color,
    }


def get_account_or_404(db: Session, account_id: int):
    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def get_playlist_or_404(db: Session, account_id: int, playlist_id: int):
    playlist = (
        db.query(Playlist)
        .filter(Playlist.id == playlist_id, Playlist.account_id == account_id)
        .first()
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


def safe_set(model, field: str, value):
    if hasattr(model, field):
        setattr(model, field, value)


def refresh_spotify_access_token(db: Session, account: SpotifyAccount) -> str:
    if not account.refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Spotify access token expired. Reconnect this Spotify account.",
        )

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET in backend .env",
        )

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=20,
    )

    if not response.ok:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to refresh Spotify token: {response.text}",
        )

    data = response.json()
    access_token = data.get("access_token")

    if not access_token:
        raise HTTPException(status_code=401, detail="Spotify did not return access token")

    account.access_token = access_token

    if data.get("refresh_token"):
        account.refresh_token = data["refresh_token"]

    db.add(account)
    db.commit()
    db.refresh(account)

    return access_token


def spotify_request(db: Session, account: SpotifyAccount, method: str, url: str, **kwargs):
    """Make a Spotify Web API request with token refresh, cooldown, and 429 retry handling."""
    access_token = account.access_token or refresh_spotify_access_token(db, account)
    headers = kwargs.pop("headers", {})
    timeout = kwargs.pop("timeout", 30)

    last_response = None

    for attempt in range(max(1, SPOTIFY_MAX_RETRIES)):
        sleep_between_spotify_requests()

        response = requests.request(
            method,
            url,
            headers={**headers, "Authorization": f"Bearer {access_token}"},
            timeout=timeout,
            **kwargs,
        )
        last_response = response

        if response.status_code == 401:
            access_token = refresh_spotify_access_token(db, account)
            sleep_between_spotify_requests()

            response = requests.request(
                method,
                url,
                headers={**headers, "Authorization": f"Bearer {access_token}"},
                timeout=timeout,
                **kwargs,
            )
            last_response = response

        if response.status_code != 429:
            return response

        retry_after = parse_retry_after_seconds(response.headers.get("Retry-After"))
        wait_seconds = retry_after + 1

        print(
            f"Spotify rate limited account={getattr(account, 'id', 'unknown')} "
            f"attempt={attempt + 1}/{SPOTIFY_MAX_RETRIES}; waiting {wait_seconds}s"
        )
        time.sleep(wait_seconds)

    return last_response


def get_history_rows(db: Session, playlist_id: int, limit: int | None = None):
    query = (
        db.query(FollowerHistory)
        .filter(FollowerHistory.playlist_id == playlist_id)
        .order_by(FollowerHistory.created_at.desc())
    )

    if limit:
        query = query.limit(limit)

    return query.all()


def get_history_rows_for_playlists(
    db: Session,
    playlist_ids: list[int],
    days_back: int | None = 45,
) -> Dict[int, list[FollowerHistory]]:
    """Load follower history for many playlists in one database query.

    This avoids the old N+1 pattern where the playlist page queried
    follower_history once per playlist. Keeping the default window at 45 days
    gives enough data for 24h / 7D / 30D calculations and daily columns.
    """
    if not playlist_ids:
        return {}

    query = db.query(FollowerHistory).filter(FollowerHistory.playlist_id.in_(playlist_ids))

    if days_back is not None:
        cutoff_date = datetime.utcnow().date() - timedelta(days=days_back)
        cutoff_datetime = datetime.combine(cutoff_date, datetime.min.time())

        if hasattr(FollowerHistory, "date"):
            query = query.filter(FollowerHistory.date >= cutoff_date)
        else:
            query = query.filter(FollowerHistory.created_at >= cutoff_datetime)

    rows = (
        query.order_by(FollowerHistory.playlist_id.asc(), FollowerHistory.created_at.desc())
        .all()
    )

    grouped: Dict[int, list[FollowerHistory]] = defaultdict(list)
    for row in rows:
        grouped[row.playlist_id].append(row)

    return dict(grouped)


def serialize_playlist_batch(
    playlists: list[Playlist],
    history_by_playlist_id: Dict[int, list[FollowerHistory]] | None = None,
    meta_by_playlist_id: Dict[int, AdsMeta] | None = None,
):
    history_by_playlist_id = history_by_playlist_id or {}
    meta_by_playlist_id = meta_by_playlist_id or {}

    return [
        serialize_playlist(
            playlist,
            history_by_playlist_id.get(playlist.id, []),
            meta_by_playlist_id.get(playlist.id),
        )
        for playlist in playlists
    ]


def closest_followers_at_or_before(history_rows, target: datetime):
    for row in history_rows:
        if row.created_at and row.created_at <= target:
            return row.followers or 0
    return None


def today_utc_date():
    return datetime.now(timezone.utc).date()


def compute_growth_stats(playlist: Playlist, history_rows):
    """Calculate summaries from daily growth rows.

    follower_history.followers stores the daily growth value in this project,
    not a historical total follower snapshot. Therefore 7D and 30D must be
    sums of daily values, never current followers minus a history row.
    """
    current = getattr(playlist, "followers", 0) or 0
    today = datetime.utcnow().date()
    latest_by_date = _latest_history_by_date(history_rows)

    def sum_days(days: int) -> int:
        total = 0
        for offset in range(days):
            day_key = (today - timedelta(days=offset)).isoformat()
            row = latest_by_date.get(day_key)
            if row:
                total += int(getattr(row, "followers", 0) or 0)
        return total

    growth_24h = sum_days(1)
    growth_7d = sum_days(7)
    growth_30d = sum_days(30)

    return {
        "followers": current,
        "growth": growth_24h,
        "growth_24h": growth_24h,
        "growth_7d": growth_7d,
        "growth_30d": growth_30d,
    }


def _history_row_date(row):
    """Return the calendar date for a follower history row.

    Prefer the explicit `date` column because imported CSV history uses that.
    Fall back to created_at for older rows.
    """
    row_date = getattr(row, "date", None)

    if not row_date and getattr(row, "created_at", None):
        row_date = row.created_at.date()

    if not row_date:
        return None

    if hasattr(row_date, "date"):
        row_date = row_date.date()

    return row_date


def _latest_history_by_date(history_rows):
    """Keep one history row per date, choosing the latest created_at row."""
    latest_by_date = {}

    for row in history_rows or []:
        row_date = _history_row_date(row)
        if not row_date:
            continue

        key = row_date.isoformat()
        existing = latest_by_date.get(key)

        if existing is None:
            latest_by_date[key] = row
            continue

        existing_created = getattr(existing, "created_at", None)
        row_created = getattr(row, "created_at", None)

        if row_created and existing_created:
            if row_created > existing_created:
                latest_by_date[key] = row
        elif row_created and not existing_created:
            latest_by_date[key] = row

    return latest_by_date


def compute_daily_growth_stats(playlist: Playlist, history_rows, days: int = 30):
    """Return imported/synced daily playlist stats.

    follower_history.followers is the daily stat value used by the frontend.
    Do not subtract one follower_history row from another here.
    """
    by_date = _latest_history_by_date(history_rows)
    today = datetime.utcnow().date()

    values = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        row = by_date.get(day.isoformat())

        growth_value = 0
        if row:
            growth_value = getattr(row, "followers", 0) or 0

        values.append({
            "date": day.isoformat(),
            "label": f"{day.month}/{day.day}",
            "growth": growth_value,
        })

    return values


def build_daily_history(history_rows):
    """Return imported/synced daily stat history using follower_history.followers directly."""
    latest_by_date = _latest_history_by_date(history_rows)

    return [
        {
            "date": current_date,
            "followers": getattr(latest_by_date[current_date], "followers", 0) or 0,
            "growth": getattr(latest_by_date[current_date], "followers", 0) or 0,
            "value": getattr(latest_by_date[current_date], "followers", 0) or 0,
            "count": getattr(latest_by_date[current_date], "followers", 0) or 0,
        }
        for current_date in sorted(latest_by_date.keys(), reverse=True)
    ]


def serialize_playlist(playlist: Playlist, history_rows=None, ads_meta: AdsMeta | None = None):
    spotify_id = getattr(playlist, "spotify_id", None)
    history_rows = history_rows or []
    growth = compute_growth_stats(playlist, history_rows)
    daily_growth = compute_daily_growth_stats(playlist, history_rows, 30)
    daily_history = build_daily_history(history_rows)

    tracks_count = (
        getattr(playlist, "tracks_count", 0)
        or getattr(playlist, "tracks_total", 0)
        or getattr(playlist, "total_tracks", 0)
        or 0
    )

    return {
        "id": playlist.id,
        "account_id": playlist.account_id,
        "spotify_id": spotify_id,
        "spotify_playlist_id": spotify_id,
        "name": getattr(playlist, "name", "Untitled Playlist"),
        "description": getattr(playlist, "description", None),
        "followers": growth["followers"],
        "growth": growth["growth"],
        "growth_24h": growth["growth_24h"],
        "growth_7d": growth["growth_7d"],
        "growth_30d": growth["growth_30d"],
        "daily_growth": daily_growth,
        "daily_history": daily_history,
        "today": daily_growth[0]["growth"] if len(daily_growth) > 0 else 0,
        "today_growth": daily_growth[0]["growth"] if len(daily_growth) > 0 else 0,
        "growth_today": daily_growth[0]["growth"] if len(daily_growth) > 0 else 0,
        "today_minus_1": daily_growth[1]["growth"] if len(daily_growth) > 1 else 0,
        "today_minus_2": daily_growth[2]["growth"] if len(daily_growth) > 2 else 0,
        "today_minus_3": daily_growth[3]["growth"] if len(daily_growth) > 3 else 0,
        "today_minus_4": daily_growth[4]["growth"] if len(daily_growth) > 4 else 0,
        "tracks_count": tracks_count,
        "tracks_total": tracks_count,
        "total_tracks": tracks_count,
        "genre": getattr(playlist, "genre", None),
        "image_url": getattr(playlist, "image_url", None),
        "spotify_url": getattr(playlist, "spotify_url", None)
        or (f"https://open.spotify.com/playlist/{spotify_id}" if spotify_id else None),
        "updated_at": playlist.updated_at.isoformat()
        if getattr(playlist, "updated_at", None)
        else None,
        "created_at": playlist.created_at.isoformat()
        if getattr(playlist, "created_at", None)
        else None,
        "ads_meta": serialize_ads_meta(ads_meta),
    }


def fetch_spotify_account_playlists(db: Session, account: SpotifyAccount):
    all_items = []
    url = "https://api.spotify.com/v1/me/playlists"

    while url:
        response = spotify_request(
            db,
            account,
            "GET",
            url,
            params={"limit": 50},  # max allowed
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify playlists fetch failed: {response.text}",
            )

        data = response.json()

        items = data.get("items", [])
        all_items.extend(items)

        # 🔥 THIS IS THE KEY FIX
        url = data.get("next")

    return all_items


def fetch_spotify_playlist_detail(db: Session, account: SpotifyAccount, spotify_playlist_id: str):
    """Fetch a fresh playlist snapshot including the current Spotify save count.

    Spotify's fields syntax for nested objects must explicitly request
    followers(total). The previous request used `followers,total`, which can
    return incomplete follower data and leave the database with an old value.
    """
    endpoint = f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}"

    response = spotify_request(
        db,
        account,
        "GET",
        endpoint,
        params={
            "fields": (
                "id,name,description,followers(total),images,"
                "tracks(total),external_urls"
            )
        },
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30,
    )

    if response.ok:
        payload = response.json()
        followers_total = ((payload.get("followers") or {}).get("total"))
        if followers_total is not None:
            return payload

    # Fallback: request the full object when Spotify omits nested fields.
    fallback = spotify_request(
        db,
        account,
        "GET",
        endpoint,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30,
    )

    if not fallback.ok:
        return None

    return fallback.json()


def update_playlist_from_spotify_item(playlist: Playlist, item: dict, detail: dict | None = None):
    source = detail or item

    images = source.get("images") or []
    image_url = images[0].get("url") if images else None
    tracks_total = (source.get("tracks") or {}).get("total") or 0
    external_urls = source.get("external_urls") or {}
    spotify_url = external_urls.get("spotify")
    followers = (source.get("followers") or {}).get("total")

    source_spotify_id = source.get("id") or item.get("id")
    if source_spotify_id:
        safe_set(playlist, "spotify_id", source_spotify_id)
        safe_set(playlist, "spotify_playlist_id", source_spotify_id)

    safe_set(playlist, "name", source.get("name") or item.get("name") or "Untitled Playlist")
    safe_set(playlist, "description", source.get("description") or item.get("description"))
    safe_set(playlist, "image_url", image_url)
    safe_set(playlist, "spotify_url", spotify_url)
    safe_set(playlist, "external_url", spotify_url)
    safe_set(playlist, "tracks_count", tracks_total)
    safe_set(playlist, "tracks_total", tracks_total)
    safe_set(playlist, "total_tracks", tracks_total)

    if followers is not None:
      safe_set(playlist, "followers", followers)

    safe_set(playlist, "updated_at", datetime.utcnow())



def extract_spotify_followers(item: dict | None, detail: dict | None = None):
    """Return the latest Spotify save count from any supported payload shape."""
    candidates = []

    for source in (detail, item):
        if not isinstance(source, dict):
            continue

        followers_payload = source.get("followers")
        if isinstance(followers_payload, dict):
            candidates.append(followers_payload.get("total"))

        candidates.extend(
            [
                source.get("followers_total"),
                source.get("saves"),
                source.get("save_count"),
            ]
        )

    for value in candidates:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue

    return None


def calculate_daily_growth_from_totals(previous_total, current_total) -> int:
    """Calculate daily growth safely from total follower snapshots.

    follower_history.followers is used by the frontend as the daily value,
    so Spotify total followers must never be inserted directly into that table.
    """
    if current_total is None:
        return 0

    try:
        current_total = int(current_total)
    except Exception:
        return 0

    if previous_total is None:
        return 0

    try:
        previous_total = int(previous_total)
    except Exception:
        return 0

    delta = current_total - previous_total

    # Protect the daily table from accidental total-follower inserts.
    # Small negative values are allowed because playlists can lose followers.
    if abs(delta) > SPOTIFY_SUSPICIOUS_DAILY_VALUE_LIMIT:
        print(
            f"Skipped suspicious daily growth value: previous={previous_total} "
            f"current={current_total} delta={delta}"
        )
        return 0

    return delta


def get_existing_history_for_date(db: Session, playlist_id: int, target_date):
    """Find an existing follower_history row for playlist/date, preferring latest created_at."""
    query = db.query(FollowerHistory).filter(FollowerHistory.playlist_id == playlist_id)

    if hasattr(FollowerHistory, "date"):
        query = query.filter(FollowerHistory.date == target_date)
    else:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)
        query = query.filter(
            FollowerHistory.created_at >= start,
            FollowerHistory.created_at < end,
        )

    return query.order_by(FollowerHistory.created_at.desc()).first()


def save_daily_growth_history(db: Session, playlist_id: int, daily_growth: int, now: datetime):
    """Insert or accumulate the daily growth value for a playlist.

    If sync runs multiple times in the same day, the new delta is added to the
    existing daily row instead of replacing it with zero.
    """
    target_date = now.date()
    existing = get_existing_history_for_date(db, playlist_id, target_date)

    if existing:
        existing.followers = (existing.followers or 0) + (daily_growth or 0)
        existing.created_at = now
        if hasattr(existing, "date"):
            existing.date = target_date
        db.add(existing)
        return existing, False

    history = FollowerHistory(
        playlist_id=playlist_id,
        followers=daily_growth or 0,
        created_at=now,
    )
    if hasattr(history, "date"):
        history.date = target_date

    db.add(history)
    return history, True


def get_existing_history_for_date_map(db: Session, playlist_ids: list[int], target_date):
    """Load today's follower_history rows for many playlists with one query."""
    if not playlist_ids:
        return {}

    query = db.query(FollowerHistory).filter(FollowerHistory.playlist_id.in_(playlist_ids))

    if hasattr(FollowerHistory, "date"):
        query = query.filter(FollowerHistory.date == target_date)
    else:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)
        query = query.filter(
            FollowerHistory.created_at >= start,
            FollowerHistory.created_at < end,
        )

    rows = query.order_by(FollowerHistory.playlist_id.asc(), FollowerHistory.created_at.desc()).all()

    latest_by_playlist_id = {}
    for row in rows:
        if row.playlist_id not in latest_by_playlist_id:
            latest_by_playlist_id[row.playlist_id] = row

    return latest_by_playlist_id


def save_daily_growth_history_from_map(
    db: Session,
    playlist_id: int,
    daily_growth: int,
    now: datetime,
    existing_by_playlist_id: Dict[int, FollowerHistory],
):
    """Insert/update daily growth using a preloaded map to avoid one query per playlist."""
    target_date = now.date()
    existing = existing_by_playlist_id.get(playlist_id)

    if existing:
        existing.followers = (existing.followers or 0) + (daily_growth or 0)
        existing.created_at = now
        if hasattr(existing, "date"):
            existing.date = target_date
        db.add(existing)
        return existing, False

    history = FollowerHistory(
        playlist_id=playlist_id,
        followers=daily_growth or 0,
        created_at=now,
    )
    if hasattr(history, "date"):
        history.date = target_date

    db.add(history)
    existing_by_playlist_id[playlist_id] = history
    return history, True


def refresh_account_playlists_from_spotify(db: Session, account_id: int):
    account = get_account_or_404(db, account_id)
    spotify_items = fetch_spotify_account_playlists(db, account)

    imported = 0
    updated = 0
    history_inserted = 0
    history_updated = 0
    now = datetime.utcnow()

    spotify_ids = [item.get("id") for item in spotify_items if item.get("id")]

    existing_playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id, Playlist.spotify_id.in_(spotify_ids))
        .all()
        if spotify_ids
        else []
    )
    playlist_by_spotify_id = {playlist.spotify_id: playlist for playlist in existing_playlists}
    existing_history_by_playlist_id = get_existing_history_for_date_map(
        db,
        [playlist.id for playlist in existing_playlists if playlist.id],
        now.date(),
    )

    for item in spotify_items:
        spotify_id = item.get("id")
        if not spotify_id:
            continue

        playlist = playlist_by_spotify_id.get(spotify_id)
        is_new_playlist = playlist is None

        if playlist:
            updated += 1
        else:
            playlist = Playlist(
                account_id=account_id,
                spotify_id=spotify_id,
                spotify_playlist_id=spotify_id,
                name=item.get("name") or "Untitled Playlist",
            )
            imported += 1

        previous_total = None if is_new_playlist else getattr(playlist, "followers", None)

        detail = None
        current_total = None

        for attempt in range(3):
            detail = fetch_spotify_playlist_detail(db, account, spotify_id)
            current_total = extract_spotify_followers(item, detail)

            if current_total is not None:
                break

            logger.warning(
                "Playlist '%s' (%s): missing follower count from Spotify (attempt %s/3)",
                item.get("name"),
                spotify_id,
                attempt + 1,
            )
            time.sleep(2)

        if current_total is None:
            logger.error(
                "Skipping playlist '%s' (%s): Spotify never returned follower count.",
                item.get("name"),
                spotify_id,
            )
            continue

        daily_growth = calculate_daily_growth_from_totals(previous_total, current_total)

        logger.info(
            "SYNC | %s | previous=%s current=%s growth=%s",
            item.get("name"),
            previous_total,
            current_total,
            daily_growth,
        )

        update_playlist_from_spotify_item(playlist, item, detail)

        # playlist.followers stores the current Spotify total.
        # follower_history.followers stores the daily growth value only.
        if current_total is not None:
            safe_set(playlist, "followers", current_total)

        db.add(playlist)
        db.flush()

        _, inserted = save_daily_growth_history_from_map(
            db,
            playlist.id,
            daily_growth,
            now,
            existing_history_by_playlist_id,
        )
        if inserted:
            history_inserted += 1
        else:
            history_updated += 1

    db.commit()

    return {
        "imported": imported,
        "updated": updated,
        "history_inserted": history_inserted,
        "history_updated": history_updated,
        "total": len(spotify_items),
    }


def fetch_playlist_tracks_from_spotify(db: Session, account: SpotifyAccount, spotify_playlist_id: str):
    tracks = []
    offset = 0
    limit = 100

    while True:
        response = spotify_request(
            db,
            account,
            "GET",
            f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}/tracks",
            params={"limit": limit, "offset": offset},
            timeout=30,
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify track fetch failed: {response.text}",
            )

        payload = response.json()

        for item in payload.get("items") or []:
            track = item.get("track") or {}
            if not track:
                continue

            artists = track.get("artists") or []
            album = track.get("album") or {}
            images = album.get("images") or []
            external_urls = track.get("external_urls") or {}

            tracks.append(
                {
                    "id": track.get("id") or f"track-{len(tracks)}",
                    "spotify_id": track.get("id"),
                    "name": track.get("name") or "Untitled Track",
                    "title": track.get("name") or "Untitled Track",
                    "artist_name": ", ".join(
                        [artist.get("name") for artist in artists if artist.get("name")]
                    ),
                    "artist": ", ".join(
                        [artist.get("name") for artist in artists if artist.get("name")]
                    ),
                    "album_name": album.get("name"),
                    "image_url": images[0].get("url") if images else None,
                    "spotify_url": external_urls.get("spotify"),
                }
            )

        if not payload.get("next"):
            break

        offset += limit

    return tracks


@router.get("/api/accounts/{account_id}/playlists")
def get_playlists_api(account_id: int, db: Session = Depends(get_db)):
    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.name.asc())
        .all()
    )

    playlist_ids = [playlist.id for playlist in playlists]
    meta_rows = (
        db.query(AdsMeta)
        .filter(AdsMeta.playlist_id.in_(playlist_ids))
        .all()
        if playlist_ids
        else []
    )
    meta_by_playlist_id = {meta.playlist_id: meta for meta in meta_rows}

    history_by_playlist_id = get_history_rows_for_playlists(db, playlist_ids, days_back=45)
    items = serialize_playlist_batch(playlists, history_by_playlist_id, meta_by_playlist_id)
    return {"items": items, "playlists": items}


@router.get("/accounts/{account_id}/playlists")
def get_playlists_legacy(account_id: int, db: Session = Depends(get_db)):
    return get_playlists_api(account_id, db)


@router.get("/api/accounts/{account_id}/playlists/{playlist_id}")
def get_playlist_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    playlist = get_playlist_or_404(db, account_id, playlist_id)
    history_rows = get_history_rows(db, playlist.id)
    ads_meta = db.query(AdsMeta).filter(AdsMeta.playlist_id == playlist.id).first()
    return serialize_playlist(playlist, history_rows, ads_meta)


@router.post("/api/accounts/{account_id}/playlists/sync")
def sync_account_playlists_api(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    result = refresh_account_playlists_from_spotify(db, account_id)

    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    playlist_ids = [playlist.id for playlist in playlists]
    meta_rows = (
        db.query(AdsMeta).filter(AdsMeta.playlist_id.in_(playlist_ids)).all()
        if playlist_ids
        else []
    )
    meta_by_playlist_id = {meta.playlist_id: meta for meta in meta_rows}

    return {
        "message": "Spotify playlists refreshed",
        "imported": result["imported"],
        "updated": result["updated"],
        "total": result["total"],
        "history_inserted": result.get("history_inserted", 0),
        "history_updated": result.get("history_updated", 0),
        "items": serialize_playlist_batch(
            playlists,
            get_history_rows_for_playlists(db, playlist_ids, days_back=45),
            meta_by_playlist_id,
        ),
    }

@router.post("/accounts/{account_id}/playlists/sync")
def sync_account_playlists_legacy(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return sync_account_playlists_api(account_id, limit, offset, db)

@router.get("/accounts/{account_id}/playlists/{playlist_id}")
def get_playlist_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return get_playlist_api(account_id, playlist_id, db)


@router.get("/api/accounts/{account_id}/playlists/{playlist_id}/history")
def get_playlist_history_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    playlist = get_playlist_or_404(db, account_id, playlist_id)
    history_rows = get_history_rows(db, playlist.id, limit=60)

    items = [
        {
            "id": row.id,
            "playlist_id": row.playlist_id,
            "followers": row.followers or 0,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "date": row.created_at.isoformat() if row.created_at else None,
        }
        for row in history_rows
    ]

    return {
        "playlist": serialize_playlist(playlist, history_rows),
        "items": items,
        "history": items,
    }


@router.get("/accounts/{account_id}/playlists/{playlist_id}/history")
def get_playlist_history_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return get_playlist_history_api(account_id, playlist_id, db)


@router.get("/api/accounts/{account_id}/playlists/{playlist_id}/tracks")
def get_playlist_tracks_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    account = get_account_or_404(db, account_id)
    playlist = get_playlist_or_404(db, account_id, playlist_id)

    spotify_id = getattr(playlist, "spotify_id", None)
    if not spotify_id:
        raise HTTPException(status_code=400, detail="No Spotify playlist ID")

    tracks = fetch_playlist_tracks_from_spotify(db, account, spotify_id)

    safe_set(playlist, "tracks_count", len(tracks))
    safe_set(playlist, "tracks_total", len(tracks))
    safe_set(playlist, "total_tracks", len(tracks))
    safe_set(playlist, "updated_at", datetime.utcnow())

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "playlist": serialize_playlist(playlist, get_history_rows(db, playlist.id)),
        "items": tracks,
        "tracks": tracks,
    }


@router.get("/accounts/{account_id}/playlists/{playlist_id}/tracks")
def get_playlist_tracks_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return get_playlist_tracks_api(account_id, playlist_id, db)

@router.post("/api/accounts/{account_id}/playlists/{playlist_id}/sync")
def sync_playlist_api(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    account = get_account_or_404(db, account_id)
    playlist = get_playlist_or_404(db, account_id, playlist_id)

    spotify_id = getattr(playlist, "spotify_id", None)
    now = datetime.utcnow()
    daily_growth = 0

    if spotify_id:
        previous_total = getattr(playlist, "followers", None)
        detail = fetch_spotify_playlist_detail(db, account, spotify_id)
        if detail:
            current_total = extract_spotify_followers(detail, detail)
            daily_growth = calculate_daily_growth_from_totals(previous_total, current_total)

            update_playlist_from_spotify_item(playlist, detail, detail)

            # playlist.followers stores the current Spotify total.
            # follower_history.followers stores the daily growth value only.
            if current_total is not None:
                safe_set(playlist, "followers", current_total)

    try:
        db.add(playlist)
        db.flush()
        save_daily_growth_history(db, playlist.id, daily_growth, now)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Failed to sync playlist history")
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to sync playlist history: {exc}")

    db.refresh(playlist)
    return {"message": "Playlist synced", "playlist": serialize_playlist(playlist, get_history_rows(db, playlist.id))}


@router.post("/accounts/{account_id}/playlists/{playlist_id}/sync")
def sync_playlist_legacy(account_id: int, playlist_id: int, db: Session = Depends(get_db)):
    return sync_playlist_api(account_id, playlist_id, db)


class ReplacePlaylistTrackPayload(BaseModel):
    id: str | None = None
    spotify_id: str | None = None
    title: str | None = None
    name: str | None = None
    artist: str | None = None
    artist_name: str | None = None


class ReplacePlaylistTracksRequest(BaseModel):
    tracks: List[ReplacePlaylistTrackPayload]


def resolve_track_uri(db: Session, track: ReplacePlaylistTrackPayload, account: SpotifyAccount):
    if track.spotify_id:
        return f"spotify:track:{track.spotify_id}"

    if track.id and len(track.id) >= 16 and not track.id.startswith(("track-", "typed-", "curation-")):
        return f"spotify:track:{track.id}"

    title = track.title or track.name
    artist = track.artist or track.artist_name

    if not title:
        return None

    query = f"{title} {artist}" if artist else title

    response = spotify_request(
        db,
        account,
        "GET",
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "track", "limit": 1},
    )

    if not response.ok:
        return None

    items = response.json().get("tracks", {}).get("items", [])
    if not items:
        return None

    spotify_id = items[0].get("id")
    if not spotify_id:
        return None

    return f"spotify:track:{spotify_id}"


def clear_playlist_on_spotify(db: Session, account: SpotifyAccount, spotify_playlist_id: str):
    response = spotify_request(
        db,
        account,
        "PUT",
        f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}/tracks",
        json={"uris": []},
    )

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Spotify clear playlist failed: {response.text}",
        )


def add_tracks_to_spotify_playlist(db: Session, account: SpotifyAccount, spotify_playlist_id: str, track_uris: list[str]):
    for index in range(0, len(track_uris), 100):
        chunk = track_uris[index:index + 100]

        response = spotify_request(
            db,
            account,
            "POST",
            f"https://api.spotify.com/v1/playlists/{spotify_playlist_id}/tracks",
            json={"uris": chunk},
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Spotify add tracks failed: {response.text}",
            )


def replace_playlist_tracks_logic(account_id: int, playlist_id: int, payload: ReplacePlaylistTracksRequest, db: Session):
    account = get_account_or_404(db, account_id)
    playlist = get_playlist_or_404(db, account_id, playlist_id)

    spotify_playlist_id = getattr(playlist, "spotify_id", None)

    if not spotify_playlist_id:
        raise HTTPException(status_code=400, detail="No Spotify playlist ID")

    track_uris = []
    unresolved = []

    for track in payload.tracks:
        uri = resolve_track_uri(db, track, account)
        if uri:
            track_uris.append(uri)
        else:
            unresolved.append(
                {
                    "title": track.title or track.name,
                    "artist": track.artist or track.artist_name,
                }
            )

    if not track_uris:
        raise HTTPException(status_code=400, detail="No valid tracks found")

    clear_playlist_on_spotify(db, account, spotify_playlist_id)
    add_tracks_to_spotify_playlist(db, account, spotify_playlist_id, track_uris)

    safe_set(playlist, "tracks_count", len(track_uris))
    safe_set(playlist, "tracks_total", len(track_uris))
    safe_set(playlist, "total_tracks", len(track_uris))
    safe_set(playlist, "updated_at", datetime.utcnow())

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "message": "Playlist cleared and replaced successfully",
        "playlist": serialize_playlist(playlist, get_history_rows(db, playlist.id)),
        "synced_tracks": len(track_uris),
        "unresolved_tracks": unresolved,
    }


@router.post("/api/accounts/{account_id}/playlists/{playlist_id}/replace-tracks")
def replace_playlist_tracks(
    account_id: int,
    playlist_id: int,
    payload: ReplacePlaylistTracksRequest,
    db: Session = Depends(get_db),
):
    return replace_playlist_tracks_logic(account_id, playlist_id, payload, db)


@router.post("/accounts/{account_id}/playlists/{playlist_id}/replace-tracks")
def replace_playlist_tracks_legacy(
    account_id: int,
    playlist_id: int,
    payload: ReplacePlaylistTracksRequest,
    db: Session = Depends(get_db),
):
    return replace_playlist_tracks_logic(account_id, playlist_id, payload, db)


def get_sync_all_status():
    with SYNC_ALL_LOCK:
        return {
            "in_progress": SYNC_ALL_STATE["in_progress"],
            "started_at": SYNC_ALL_STATE["started_at"],
            "finished_at": SYNC_ALL_STATE["finished_at"],
            "last_result": SYNC_ALL_STATE["last_result"],
            "cooldown_seconds": SYNC_ALL_COOLDOWN_SECONDS,
        }


@router.post("/api/playlists/sync-all")
def sync_all_playlists_api(db: Session = Depends(get_db)):
    """Run Sync All directly for dashboard/manual use.

    Protections added:
    - Only one Sync All can run at a time in this backend process.
    - A cooldown blocks accidental repeated clicks/refreshes.
    - Database reads/writes inside account sync are batched where possible.
    """
    now_ts = time.time()

    with SYNC_ALL_LOCK:
        if SYNC_ALL_STATE["in_progress"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Sync All is already running",
                    "status": get_sync_all_status(),
                },
            )

        finished_at = SYNC_ALL_STATE.get("finished_at")
        if finished_at and SYNC_ALL_COOLDOWN_SECONDS > 0:
            elapsed = now_ts - finished_at
            if elapsed < SYNC_ALL_COOLDOWN_SECONDS:
                retry_after = int(SYNC_ALL_COOLDOWN_SECONDS - elapsed)
                raise HTTPException(
                    status_code=429,
                    detail={
                        "message": f"Sync All cooldown active. Try again in {retry_after}s.",
                        "retry_after_seconds": retry_after,
                        "status": get_sync_all_status(),
                    },
                )

        SYNC_ALL_STATE["in_progress"] = True
        SYNC_ALL_STATE["started_at"] = datetime.utcnow().isoformat()

    try:
        account_ids = [
            account.id
            for account in db.query(SpotifyAccount).order_by(SpotifyAccount.id.asc()).all()
        ]

        synced = 0
        failed = []
        history_inserted = 0
        history_updated = 0
        total_playlists = 0

        for index, account_id in enumerate(account_ids):
            try:
                result = refresh_account_playlists_from_spotify(db, account_id)
                synced += 1
                history_inserted += result.get("history_inserted", 0)
                history_updated += result.get("history_updated", 0)
                total_playlists += result.get("total", 0)
                print(f"Synced account {account_id}: {result}")
            except Exception as exc:
                db.rollback()
                failed.append({"account_id": account_id, "error": str(exc)})
                print(f"Sync failed for account {account_id}: {exc}")

            if index < len(account_ids) - 1 and SPOTIFY_ACCOUNT_SYNC_COOLDOWN_SECONDS > 0:
                time.sleep(SPOTIFY_ACCOUNT_SYNC_COOLDOWN_SECONDS)

        result_payload = {
            "success": len(failed) == 0,
            "message": "Sync completed",
            "source": "DATABASE_URL",
            "syncProtection": "lock-and-cooldown-v1",
            "accounts": len(account_ids),
            "synced": synced,
            "failed": failed,
            "history_inserted": history_inserted,
            "history_updated": history_updated,
            "total_playlists": total_playlists,
        }
        return result_payload

    finally:
        with SYNC_ALL_LOCK:
            SYNC_ALL_STATE["in_progress"] = False
            SYNC_ALL_STATE["finished_at"] = time.time()
            SYNC_ALL_STATE["last_result"] = locals().get("result_payload")


@router.get("/api/playlists/sync-all")
def sync_all_playlists_get_api():
    """GET returns status only. Use POST to start Sync All.

    This prevents accidental browser refreshes or prefetches from starting a
    heavy Spotify sync job.
    """
    return {"success": True, "message": "Use POST to start Sync All", "status": get_sync_all_status()}


class CreatePlaylistRequest(BaseModel):
    account_id: int
    name: str
    description: str | None = None
    import_tracks_url: str | None = None
    import_tracks_urls: list[str] | None = None


class BulkCreatePlaylistRow(BaseModel):
    account_id: int
    name: str
    description: str | None = None
    import_tracks_url: str | None = None
    import_tracks_urls: list[str] | None = None


class BulkCreatePlaylistsRequest(BaseModel):
    rows: list[BulkCreatePlaylistRow]


def extract_spotify_playlist_id(value: str | None):
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    patterns = [
        r"open\.spotify\.com/playlist/([A-Za-z0-9]+)",
        r"spotify:playlist:([A-Za-z0-9]+)",
        r"playlist/([A-Za-z0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    if re.fullmatch(r"[A-Za-z0-9]{16,}", text):
        return text

    return None


def get_spotify_user_id(db: Session, account: SpotifyAccount):
    for field in ["spotify_user_id", "spotify_id", "user_id"]:
        value = getattr(account, field, None)
        if value:
            return value

    response = spotify_request(db, account, "GET", "https://api.spotify.com/v1/me")
    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Spotify profile fetch failed: {response.text}",
        )

    user_id = response.json().get("id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Spotify did not return user id")

    return user_id


def collect_import_track_uris(db: Session, account: SpotifyAccount, urls: list[str]):
    track_uris: list[str] = []
    unresolved: list[str] = []

    for url in urls:
        source_playlist_id = extract_spotify_playlist_id(url)
        if not source_playlist_id:
            unresolved.append(url)
            continue

        try:
            tracks = fetch_playlist_tracks_from_spotify(db, account, source_playlist_id)
            for track in tracks:
                spotify_id = track.get("spotify_id") or track.get("id")
                if spotify_id:
                    track_uris.append(f"spotify:track:{spotify_id}")
        except Exception:
            unresolved.append(url)

    # Spotify allows max 100 per request, but duplicate URIs are fine to dedupe here.
    deduped = list(dict.fromkeys(track_uris))
    return deduped, unresolved


def create_spotify_playlist_logic(db: Session, payload: CreatePlaylistRequest | BulkCreatePlaylistRow):
    account = get_account_or_404(db, payload.account_id)
    user_id = get_spotify_user_id(db, account)

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is required")

    response = spotify_request(
        db,
        account,
        "POST",
        f"https://api.spotify.com/v1/users/{user_id}/playlists",
        json={
            "name": name,
            "description": payload.description or "",
            "public": False,
        },
        timeout=30,
    )

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Spotify playlist creation failed: {response.text}",
        )

    spotify_playlist = response.json()
    spotify_playlist_id = spotify_playlist.get("id")
    spotify_url = (spotify_playlist.get("external_urls") or {}).get("spotify")

    imported_urls = []
    if payload.import_tracks_url:
        imported_urls.append(payload.import_tracks_url)
    if payload.import_tracks_urls:
        imported_urls.extend(payload.import_tracks_urls)

    track_uris: list[str] = []
    unresolved_sources: list[str] = []
    if imported_urls and spotify_playlist_id:
        track_uris, unresolved_sources = collect_import_track_uris(db, account, imported_urls)
        if track_uris:
            add_tracks_to_spotify_playlist(db, account, spotify_playlist_id, track_uris)

    playlist = Playlist(
        account_id=account.id,
        spotify_id=spotify_playlist_id,
        spotify_playlist_id=spotify_playlist_id,
        name=spotify_playlist.get("name") or name,
        description=spotify_playlist.get("description") or payload.description,
        spotify_url=spotify_url,
        external_url=spotify_url,
        url=spotify_url,
        playlist_url=spotify_url,
        tracks_count=len(track_uris),
        tracks_total=len(track_uris),
        followers=0,
        updated_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "id": playlist.id,
        "spotify_id": spotify_playlist_id,
        "name": playlist.name,
        "account_id": account.id,
        "account_name": getattr(account, "display_name", None) or getattr(account, "name", None),
        "tracks_count": len(track_uris),
        "spotify_url": spotify_url,
        "playlist_url": spotify_url,
        "link": spotify_url,
        "unresolved_sources": unresolved_sources,
    }


@router.post("/api/playlists/create")
def create_playlist_api(payload: CreatePlaylistRequest, db: Session = Depends(get_db)):
    return create_spotify_playlist_logic(db, payload)


@router.post("/api/playlists/create-bulk")
def create_playlists_bulk_api(payload: BulkCreatePlaylistsRequest, db: Session = Depends(get_db)):
    results = []

    for row in payload.rows:
        try:
            results.append(create_spotify_playlist_logic(db, row))
        except Exception as exc:
            results.append({
                "name": row.name,
                "account_id": row.account_id,
                "tracks_count": 0,
                "id": "—",
                "link": "—",
                "error": str(exc),
            })

    return {"items": results, "results": results}


class UpdatePlaylistGenreRequest(BaseModel):
    genre: str | None = None


@router.patch("/api/playlists/{playlist_id}/genre")
def update_playlist_genre(
    playlist_id: int,
    payload: UpdatePlaylistGenreRequest,
    db: Session = Depends(get_db),
):
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist.genre = payload.genre
    playlist.updated_at = datetime.utcnow()

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return {
        "message": "Genre updated",
        "playlist_id": playlist.id,
        "genre": playlist.genre,
    }

@router.get("/api/playlists/{playlist_id}/ads-meta")
def get_ads_meta(playlist_id: int, db: Session = Depends(get_db)):
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    meta = db.query(AdsMeta).filter(AdsMeta.playlist_id == playlist_id).first()
    return {"playlist_id": playlist_id, "ads_meta": serialize_ads_meta(meta)}


@router.patch("/api/playlists/{playlist_id}/ads-meta")
def update_ads_meta(
    playlist_id: int,
    payload: UpdateAdsMetaRequest,
    db: Session = Depends(get_db),
):
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    meta = db.query(AdsMeta).filter(AdsMeta.playlist_id == playlist_id).first()

    if not meta:
        meta = AdsMeta(playlist_id=playlist_id)

    if payload.category is not None:
        meta.category = payload.category
    if payload.genre is not None:
        meta.genre = payload.genre
    if payload.country is not None:
        meta.country = payload.country
    if payload.master_playlist is not None:
        meta.master_playlist = payload.master_playlist
    if payload.ads is not None:
        meta.ads = payload.ads
    if payload.color is not None:
        meta.color = payload.color

    db.add(meta)
    db.commit()
    db.refresh(meta)

    return {
        "message": "Saved",
        "playlist_id": playlist_id,
        "ads_meta": serialize_ads_meta(meta),
    }

# Compatibility alias for older frontend naming
@router.post("/api/playlists/bulk-create")
def create_playlists_bulk_alias_api(payload: BulkCreatePlaylistsRequest, db: Session = Depends(get_db)):
    return create_playlists_bulk_api(payload, db)
