import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
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



class AdsHiddenRowsPayload(BaseModel):
    hidden_rows: dict[str, bool]


def _ensure_ads_hidden_rows_table(db: Session) -> None:
    """Create the dedicated hidden-rows table if it does not exist.

    This avoids relying on the older playlist_manager_state table schema,
    which may not contain user_key/state columns in older deployments.
    """
    if _is_postgres(db):
        db.execute(
            text("""
                CREATE TABLE IF NOT EXISTS ads_hidden_rows_state (
                    id INTEGER PRIMARY KEY,
                    hidden_rows JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        )
    else:
        db.execute(
            text("""
                CREATE TABLE IF NOT EXISTS ads_hidden_rows_state (
                    id INTEGER PRIMARY KEY,
                    hidden_rows TEXT NOT NULL DEFAULT '{}',
                    updated_at DATETIME NOT NULL
                )
            """)
        )
    db.commit()


@router.get("/ads-hidden-rows")
def get_ads_hidden_rows(db: Session = Depends(get_db)):
    _ensure_ads_hidden_rows_table(db)

    row = db.execute(
        text("""
            SELECT hidden_rows
            FROM ads_hidden_rows_state
            WHERE id = 1
            LIMIT 1
        """)
    ).first()

    if not row:
        return {"success": True, "hidden_rows": {}}

    value = row.hidden_rows
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}

    if not isinstance(value, dict):
        value = {}

    return {"success": True, "hidden_rows": value}


@router.post("/ads-hidden-rows")
def save_ads_hidden_rows(
    payload: AdsHiddenRowsPayload,
    db: Session = Depends(get_db),
):
    _ensure_ads_hidden_rows_table(db)

    now = datetime.now(timezone.utc)
    hidden_rows_json = json.dumps(payload.hidden_rows)

    try:
        if _is_postgres(db):
            db.execute(
                text("""
                    INSERT INTO ads_hidden_rows_state (id, hidden_rows, updated_at)
                    VALUES (1, CAST(:hidden_rows AS jsonb), :updated_at)
                    ON CONFLICT (id)
                    DO UPDATE SET
                        hidden_rows = EXCLUDED.hidden_rows,
                        updated_at = EXCLUDED.updated_at
                """),
                {
                    "hidden_rows": hidden_rows_json,
                    "updated_at": now,
                },
            )
        else:
            existing = db.execute(
                text("SELECT id FROM ads_hidden_rows_state WHERE id = 1")
            ).first()

            if existing:
                db.execute(
                    text("""
                        UPDATE ads_hidden_rows_state
                        SET hidden_rows = :hidden_rows, updated_at = :updated_at
                        WHERE id = 1
                    """),
                    {
                        "hidden_rows": hidden_rows_json,
                        "updated_at": now,
                    },
                )
            else:
                db.execute(
                    text("""
                        INSERT INTO ads_hidden_rows_state (id, hidden_rows, updated_at)
                        VALUES (1, :hidden_rows, :updated_at)
                    """),
                    {
                        "hidden_rows": hidden_rows_json,
                        "updated_at": now,
                    },
                )

        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save hidden playlists: {exc}",
        )

    return {
        "success": True,
        "message": "Hidden playlists saved",
        "count": sum(1 for value in payload.hidden_rows.values() if value),
    }


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
