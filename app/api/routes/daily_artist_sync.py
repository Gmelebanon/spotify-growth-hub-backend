"""Protected endpoint for an external daily cron scheduler.

Set DAILY_SYNC_SECRET in the backend and send it as X-Daily-Sync-Secret.
"""
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.routes.artist_library import sync_metadata

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.post("/daily-artist-sync")
def daily_artist_sync(
    db: Session = Depends(get_db),
    x_daily_sync_secret: str | None = Header(default=None),
):
    configured_secret = os.getenv("DAILY_SYNC_SECRET", "").strip()
    if not configured_secret:
        raise HTTPException(status_code=503, detail="Daily sync is not configured")
    if not x_daily_sync_secret or not hmac.compare_digest(x_daily_sync_secret, configured_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return sync_metadata(db=db)
