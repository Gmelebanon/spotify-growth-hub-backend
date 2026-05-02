from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.follower_history import FollowerHistory
from app.models.playlist import Playlist
from app.models.spotify_account import SpotifyAccount

router = APIRouter(prefix="/api", tags=["dashboard"])


def get_account_or_404(db: Session, account_id: int):
    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("/accounts/{account_id}/dashboard")
def get_dashboard_summary(account_id: int, db: Session = Depends(get_db)):
    try:
        get_account_or_404(db, account_id)

        playlists = db.query(Playlist).filter(Playlist.account_id == account_id).all()

        total_playlists = len(playlists)

        total_followers = sum(
            (getattr(p, "followers", 0) or 0) for p in playlists
        )

        growth_24h = 0

        for playlist in playlists:
            history_rows = (
                db.query(FollowerHistory)
                .filter(FollowerHistory.playlist_id == playlist.id)
                .order_by(FollowerHistory.created_at.desc())
                .limit(2)
                .all()
            )

            if len(history_rows) >= 2:
                latest = history_rows[0].followers or 0
                previous = history_rows[1].followers or 0
                growth_24h += latest - previous

        return {
            "total_playlists": total_playlists,
            "total_followers": total_followers,
            "growth_24h": growth_24h,
        }

    except Exception as e:
        return {
            "total_playlists": 0,
            "total_followers": 0,
            "growth_24h": 0,
            "error": str(e),
        }


@router.get("/accounts/{account_id}/dashboard/top-growth")
def get_top_playlists(account_id: int, db: Session = Depends(get_db)):
    try:
        get_account_or_404(db, account_id)

        playlists = db.query(Playlist).filter(Playlist.account_id == account_id).all()

        items = []

        for playlist in playlists:
            history_rows = (
                db.query(FollowerHistory)
                .filter(FollowerHistory.playlist_id == playlist.id)
                .order_by(FollowerHistory.created_at.desc())
                .limit(2)
                .all()
            )

            growth = 0

            if len(history_rows) >= 2:
                latest = history_rows[0].followers or 0
                previous = history_rows[1].followers or 0
                growth = latest - previous

            items.append(
                {
                    "id": playlist.id,
                    "name": getattr(playlist, "name", "Unknown"),
                    "followers": getattr(playlist, "followers", 0) or 0,
                    "growth": growth,
                }
            )

        items.sort(key=lambda x: x["growth"], reverse=True)

        return {"items": items[:10]}

    except Exception as e:
        return {"items": [], "error": str(e)}