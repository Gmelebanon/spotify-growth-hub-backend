from fastapi import APIRouter
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import SessionLocal

router = APIRouter()


@router.get("/api/ads/filter-options")
async def get_filter_options():
    db: Session = SessionLocal()

    result = db.execute(text("""
        SELECT *
        FROM ads_filter_options
        ORDER BY value ASC
    """))

    items = [dict(row._mapping) for row in result]

    db.close()

    return {"items": items}


@router.post("/api/ads/filter-options")
async def add_filter_option(payload: dict):
    db: Session = SessionLocal()

    db.execute(text("""
        INSERT INTO ads_filter_options
        (option_type, value)
        VALUES
        (:option_type, :value)
        ON CONFLICT (option_type, value)
        DO NOTHING
    """), payload)

    db.commit()
    db.close()

    return {"success": True}


@router.delete("/api/ads/filter-options/{option_id}")
async def delete_filter_option(option_id: str):
    db: Session = SessionLocal()

    db.execute(text("""
        DELETE FROM ads_filter_options
        WHERE id = :id
    """), {"id": option_id})

    db.commit()
    db.close()

    return {"success": True}