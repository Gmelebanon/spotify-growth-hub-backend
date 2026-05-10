from fastapi import APIRouter
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from sqlalchemy import text

router = APIRouter()


@router.get("/api/curation/history")
async def get_history():
    db: Session = SessionLocal()

    result = db.execute(text("""
        SELECT *
        FROM curation_import_history
        ORDER BY imported_at DESC
    """))

    rows = [dict(row._mapping) for row in result]

    db.close()

    return rows


@router.post("/api/curation/history")
async def save_history(payload: dict):
    db: Session = SessionLocal()

    db.execute(text("""
        INSERT INTO curation_import_history
        (side, url, display_name, account_name, item_type)
        VALUES
        (:side, :url, :display_name, :account_name, :item_type)
    """), payload)

    db.commit()
    db.close()

    return {"success": True}


@router.delete("/api/curation/history/{history_id}")
async def delete_history(history_id: str):
    db: Session = SessionLocal()

    db.execute(text("""
        DELETE FROM curation_import_history
        WHERE id = :id
    """), {"id": history_id})

    db.commit()
    db.close()

    return {"success": True}