import json
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

router = APIRouter()


@router.get("/api/ads/settings")
def get_ads_settings():
    db: Session = SessionLocal()
    try:
        result = db.execute(
            text(
                """
                SELECT
                    id,
                    playlist_id,
                    playlist_name,
                    account_name,
                    genre,
                    category,
                    country,
                    master_playlist,
                    ad_date,
                    campaign_status,
                    budget,
                    followers,
                    last_synced,
                    settings,
                    created_at,
                    updated_at
                FROM ads_playlist_settings
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
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
