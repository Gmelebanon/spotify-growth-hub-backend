from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(prefix="/api/sync-status", tags=["sync-status"])


class SyncStatusPayload(BaseModel):
    status: str = "success"
    source: str = "github_actions"
    message: Optional[str] = None


def ensure_sync_status_table(db: Session) -> None:
    db.execute(
        text(
            """
            create table if not exists sync_status_events (
              id bigserial primary key,
              status text not null default 'success',
              source text not null default 'github_actions',
              message text,
              synced_at timestamptz not null default now(),
              created_at timestamptz not null default now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            create index if not exists idx_sync_status_events_synced_at
            on sync_status_events (synced_at desc)
            """
        )
    )
    db.commit()


def relative_time(value: datetime | None) -> str:
    if not value:
        return "Not synced yet"

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    diff = datetime.now(timezone.utc) - value.astimezone(timezone.utc)
    seconds = max(0, int(diff.total_seconds()))

    if seconds < 60:
        return "just now"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"

    days = hours // 24
    return f"{days}d ago"


@router.get("")
def get_sync_status(db: Session = Depends(get_db)):
    ensure_sync_status_table(db)

    row = (
        db.execute(
            text(
                """
                select id, status, source, message, synced_at
                from sync_status_events
                order by synced_at desc
                limit 1
                """
            )
        )
        .mappings()
        .first()
    )

    if not row:
        return {
            "success": True,
            "lastSync": None,
            "lastSyncFreshness": "Not synced yet",
            "status": None,
            "source": None,
            "message": None,
        }

    synced_at = row["synced_at"]

    return {
        "success": True,
        "lastSync": synced_at.isoformat() if synced_at else None,
        "lastSyncFreshness": relative_time(synced_at),
        "status": row["status"],
        "source": row["source"],
        "message": row["message"],
    }


@router.post("/mark")
def mark_sync_status(payload: SyncStatusPayload, db: Session = Depends(get_db)):
    ensure_sync_status_table(db)

    synced_at = datetime.now(timezone.utc)

    row = (
        db.execute(
            text(
                """
                insert into sync_status_events (status, source, message, synced_at)
                values (:status, :source, :message, :synced_at)
                returning id, status, source, message, synced_at
                """
            ),
            {
                "status": payload.status,
                "source": payload.source,
                "message": payload.message,
                "synced_at": synced_at,
            },
        )
        .mappings()
        .first()
    )
    db.commit()

    return {
        "success": True,
        "id": row["id"],
        "lastSync": row["synced_at"].isoformat(),
        "lastSyncFreshness": "just now",
        "status": row["status"],
        "source": row["source"],
        "message": row["message"],
    }
