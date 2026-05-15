from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

router = APIRouter()


@router.get("/api/curation/csv-playlists")
def get_curation_csv_playlists(side: str | None = None):
    db: Session = SessionLocal()

    try:
        if side in {"source", "my"}:
            result = db.execute(
                text(
                    """
                    SELECT id, side, playlist_id, label, created_at
                    FROM curation_csv_playlists
                    WHERE side = :side
                    ORDER BY created_at ASC
                    """
                ),
                {"side": side},
            )
        else:
            result = db.execute(
                text(
                    """
                    SELECT id, side, playlist_id, label, created_at
                    FROM curation_csv_playlists
                    ORDER BY side ASC, created_at ASC
                    """
                )
            )

        rows = [dict(row._mapping) for row in result]
        return {"items": rows}
    finally:
        db.close()


@router.post("/api/curation/csv-playlists")
def save_curation_csv_playlists(payload: dict):
    side = payload.get("side")
    items = payload.get("items") or []

    if side not in {"source", "my"}:
        raise HTTPException(status_code=400, detail="Invalid side")

    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Items must be a list")

    db: Session = SessionLocal()

    try:
        db.execute(
            text("DELETE FROM curation_csv_playlists WHERE side = :side"),
            {"side": side},
        )

        for item in items:
            playlist_id = str(
                item.get("playlistId")
                or item.get("playlist_id")
                or item.get("spotify_playlist_id")
                or item.get("spotify_id")
                or ""
            ).strip()

            if not playlist_id:
                continue

            label = str(
                item.get("label")
                or item.get("display_name")
                or item.get("name")
                or playlist_id
            ).strip()

            db.execute(
                text(
                    """
                    INSERT INTO curation_csv_playlists
                    (side, playlist_id, label)
                    VALUES (:side, :playlist_id, :label)
                    """
                ),
                {
                    "side": side,
                    "playlist_id": playlist_id,
                    "label": label,
                },
            )

        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()
