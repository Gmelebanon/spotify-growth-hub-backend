from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

router = APIRouter()


def row_to_dict(row):
    item = dict(row._mapping)
    if item.get("id") is not None:
        item["id"] = str(item["id"])
    if item.get("imported_at") is not None and hasattr(item["imported_at"], "isoformat"):
        item["imported_at"] = item["imported_at"].isoformat()
    return item


@router.get("/api/curation/history")
def get_curation_history():
    db: Session = SessionLocal()
    try:
        result = db.execute(
            text(
                """
                SELECT id, side, url, display_name, account_name, item_type, imported_at
                FROM curation_import_history
                ORDER BY imported_at DESC
                """
            )
        )
        return [row_to_dict(row) for row in result]
    finally:
        db.close()


@router.post("/api/curation/history")
def save_curation_history(payload: dict):
    side = str(payload.get("side") or "").strip()
    url = str(payload.get("url") or "").strip()

    if side not in {"source", "my"}:
        raise HTTPException(status_code=400, detail="side must be source or my")

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    display_name = str(payload.get("display_name") or "Imported Spotify Playlist").strip()
    account_name = str(payload.get("account_name") or "Spotify").strip()
    item_type = str(payload.get("item_type") or "playlist").strip()

    db: Session = SessionLocal()
    try:
        db.execute(
            text(
                """
                DELETE FROM curation_import_history
                WHERE side = :side AND url = :url
                """
            ),
            {"side": side, "url": url},
        )

        result = db.execute(
            text(
                """
                INSERT INTO curation_import_history
                (side, url, display_name, account_name, item_type, imported_at)
                VALUES
                (:side, :url, :display_name, :account_name, :item_type, NOW())
                RETURNING id, side, url, display_name, account_name, item_type, imported_at
                """
            ),
            {
                "side": side,
                "url": url,
                "display_name": display_name,
                "account_name": account_name,
                "item_type": item_type,
            },
        )
        row = result.fetchone()
        db.commit()
        return {"success": True, "item": row_to_dict(row) if row else None}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.delete("/api/curation/history/{history_id}")
def delete_curation_history(history_id: str):
    db: Session = SessionLocal()
    try:
        db.execute(
            text(
                """
                DELETE FROM curation_import_history
                WHERE id = :id
                """
            ),
            {"id": history_id},
        )
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()
