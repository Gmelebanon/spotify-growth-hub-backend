import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(prefix="/api", tags=["playlist-manager-state"])


class PlaylistManagerStatePayload(BaseModel):
    user_key: str = "global"
    state: dict


def _state_value_for_response(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _is_postgres(db: Session) -> bool:
    try:
        return db.bind is not None and db.bind.dialect.name == "postgresql"
    except Exception:
        return False


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
        "state": _state_value_for_response(result.state),
    }


@router.post("/playlist-manager-state")
def save_playlist_manager_state(
    payload: PlaylistManagerStatePayload,
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    state_json = json.dumps(payload.state)

    if _is_postgres(db):
        update_sql = text("""
            UPDATE playlist_manager_state
            SET state = CAST(:state AS jsonb), updated_at = :updated_at
            WHERE user_key = :user_key
        """)
        insert_sql = text("""
            INSERT INTO playlist_manager_state (user_key, state, updated_at)
            VALUES (:user_key, CAST(:state AS jsonb), :updated_at)
        """)
    else:
        update_sql = text("""
            UPDATE playlist_manager_state
            SET state = :state, updated_at = :updated_at
            WHERE user_key = :user_key
        """)
        insert_sql = text("""
            INSERT INTO playlist_manager_state (user_key, state, updated_at)
            VALUES (:user_key, :state, :updated_at)
        """)

    params = {
        "user_key": payload.user_key,
        "state": state_json,
        "updated_at": now,
    }

    result = db.execute(update_sql, params)

    if result.rowcount == 0:
        db.execute(insert_sql, params)

    db.commit()

    return {
        "success": True,
        "message": "Playlist manager state saved",
    }
