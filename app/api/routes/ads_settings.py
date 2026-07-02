import json
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

router = APIRouter()


def ensure_playlist_visibility_columns(db: Session):
    db.execute(text("ALTER TABLE playlists ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE"))
    db.execute(text("ALTER TABLE playlists ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL"))
    db.execute(text("ALTER TABLE playlists ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP NULL"))
    db.commit()



@router.get("/api/ads/settings")
def get_ads_settings():
    db: Session = SessionLocal()
    try:
        ensure_playlist_visibility_columns(db)
        result = db.execute(
            text(
                """
                SELECT
                    s.id,
                    s.playlist_id,
                    COALESCE(p.name, s.playlist_name) AS playlist_name,
                    COALESCE(a.display_name, s.account_name) AS account_name,
                    s.genre,
                    s.category,
                    s.country,
                    s.master_playlist,
                    s.ad_date,
                    s.campaign_status,
                    s.budget,
                    COALESCE(p.followers, s.followers) AS followers,
                    s.last_synced,
                    s.settings,
                    s.created_at,
                    s.updated_at
                FROM ads_playlist_settings s
                JOIN playlists p
                  ON (
                    CAST(p.id AS TEXT) = s.playlist_id
                    OR p.spotify_id = s.playlist_id
                    OR p.spotify_playlist_id = s.playlist_id
                  )
                JOIN spotify_accounts a
                  ON a.id = p.account_id
                WHERE COALESCE(p.is_deleted, FALSE) = FALSE
                ORDER BY s.updated_at DESC NULLS LAST, s.created_at DESC NULLS LAST
                """
            )
        )
        return {"items": [dict(row._mapping) for row in result]}
    finally:
        db.close()


@router.post("/api/ads/settings")
def save_ads_settings(payload: dict):
    playlist_id = str(payload.get("playlist_id") or "").strip()
    if not playlist_id:
        raise HTTPException(status_code=400, detail="playlist_id is required")

    settings = payload.get("settings") or {}
    if payload.get("ads") is not None:
        settings["ads"] = payload.get("ads")
    if payload.get("color") is not None:
        settings["color"] = payload.get("color")

    db: Session = SessionLocal()
    try:
        ensure_playlist_visibility_columns(db)

        active_playlist = db.execute(
            text(
                """
                SELECT p.id
                FROM playlists p
                JOIN spotify_accounts a ON a.id = p.account_id
                WHERE (
                    CAST(p.id AS TEXT) = :playlist_id
                    OR p.spotify_id = :playlist_id
                    OR p.spotify_playlist_id = :playlist_id
                )
                AND COALESCE(p.is_deleted, FALSE) = FALSE
                LIMIT 1
                """
            ),
            {"playlist_id": playlist_id},
        ).first()

        if not active_playlist:
            raise HTTPException(status_code=404, detail="Playlist is no longer active")

        db.execute(
            text(
                """
                INSERT INTO ads_playlist_settings (
                    playlist_id,
                    playlist_name,
                    account_name,
                    category,
                    genre,
                    country,
                    master_playlist,
                    ad_date,
                    campaign_status,
                    budget,
                    followers,
                    last_synced,
                    settings,
                    updated_at
                ) VALUES (
                    :playlist_id,
                    :playlist_name,
                    :account_name,
                    :category,
                    :genre,
                    :country,
                    :master_playlist,
                    :ad_date,
                    :campaign_status,
                    :budget,
                    :followers,
                    :last_synced,
                    CAST(:settings AS JSONB),
                    NOW()
                )
                ON CONFLICT (playlist_id) DO UPDATE SET
                    playlist_name = EXCLUDED.playlist_name,
                    account_name = EXCLUDED.account_name,
                    category = EXCLUDED.category,
                    genre = EXCLUDED.genre,
                    country = EXCLUDED.country,
                    master_playlist = EXCLUDED.master_playlist,
                    ad_date = EXCLUDED.ad_date,
                    campaign_status = EXCLUDED.campaign_status,
                    budget = EXCLUDED.budget,
                    followers = EXCLUDED.followers,
                    last_synced = EXCLUDED.last_synced,
                    settings = EXCLUDED.settings,
                    updated_at = NOW()
                """
            ),
            {
                "playlist_id": playlist_id,
                "playlist_name": payload.get("playlist_name"),
                "account_name": payload.get("account_name"),
                "category": payload.get("category"),
                "genre": payload.get("genre"),
                "country": payload.get("country"),
                "master_playlist": payload.get("master_playlist"),
                "ad_date": payload.get("ad_date"),
                "campaign_status": payload.get("campaign_status"),
                "budget": payload.get("budget"),
                "followers": payload.get("followers"),
                "last_synced": payload.get("last_synced"),
                "settings": json.dumps(settings),
            },
        )
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.post("/api/ads/settings/cleanup-stale")
def cleanup_stale_ads_settings():
    """Delete Ads settings that point to playlists no longer present/active.

    This is safe because Ads settings are UI metadata only; playlist history and
    playlist rows remain untouched.
    """
    db: Session = SessionLocal()
    try:
        ensure_playlist_visibility_columns(db)
        result = db.execute(
            text(
                """
                DELETE FROM ads_playlist_settings s
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM playlists p
                    JOIN spotify_accounts a ON a.id = p.account_id
                    WHERE (
                        CAST(p.id AS TEXT) = s.playlist_id
                        OR p.spotify_id = s.playlist_id
                        OR p.spotify_playlist_id = s.playlist_id
                    )
                    AND COALESCE(p.is_deleted, FALSE) = FALSE
                )
                """
            )
        )
        db.commit()
        return {"success": True, "deleted": result.rowcount or 0}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()

