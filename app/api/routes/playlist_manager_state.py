from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(prefix="/api", tags=["playlist-manager-state"])


class PlaylistManagerStatePayload(BaseModel):
    user_key: str = "global"
    state: dict


@router.get("/playlist-manager-state")
def get_playlist_manager_state(
    user_key: str = "global",
    db: Session = Depends(get_db),
):
    result = db.execute(
        text("""
            SELECT state
            FROM playlist_manager_state
            WHERE user_key = :user_key
            LIMIT 1
        """),
        {"user_key": user_key},
    ).first()

    if not result:
        return {
            "success": True,
            "state": None,
        }

    return {
        "success": True,
        "state": result.state,
    }


@router.post("/playlist-manager-state")
def save_playlist_manager_state(
    payload: PlaylistManagerStatePayload,
    db: Session = Depends(get_db),
):
    db.execute(
        text("""
            INSERT INTO playlist_manager_state (user_key, state, updated_at)
            VALUES (:user_key, :state, :updated_at)
            ON CONFLICT (user_key)
            DO UPDATE SET
                state = EXCLUDED.state,
                updated_at = EXCLUDED.updated_at
        """),
        {
            "user_key": payload.user_key,
            "state": payload.state,
            "updated_at": datetime.utcnow(),
        },
    )

    db.commit()

    return {
        "success": True,
        "message": "Playlist manager state saved",
    }