from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.curation_save import CurationSave

router = APIRouter(tags=["curations"])


class CurationSaveRequest(BaseModel):
    id: str | None = None
    name: str
    account_id: int | None = None
    tracks: list[dict[str, Any]] = []


def serialize_curation(item: CurationSave):
    return {
        "id": item.id,
        "name": item.name,
        "account_id": item.account_id,
        "tracks": item.tracks or [],
        "trackCount": item.track_count or 0,
        "track_count": item.track_count or 0,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/api/curations")
def list_curations(
    account_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(CurationSave)
    if account_id is not None:
        query = query.filter(
            (CurationSave.account_id == account_id) | (CurationSave.account_id.is_(None))
        )

    items = query.order_by(CurationSave.updated_at.desc()).all()
    return {"items": [serialize_curation(item) for item in items]}


@router.post("/api/curations")
def save_curation(payload: CurationSaveRequest, db: Session = Depends(get_db)):
    item_id = payload.id or f"saved-{int(datetime.utcnow().timestamp() * 1000)}"
    tracks = payload.tracks or []

    item = db.query(CurationSave).filter(CurationSave.id == item_id).first()
    if not item:
        item = CurationSave(id=item_id, created_at=datetime.utcnow())

    item.name = payload.name
    item.account_id = payload.account_id
    item.tracks = tracks
    item.track_count = len(tracks)
    item.updated_at = datetime.utcnow()

    db.add(item)
    db.commit()
    db.refresh(item)

    return serialize_curation(item)


@router.patch("/api/curations/{curation_id}")
def update_curation(
    curation_id: str,
    payload: CurationSaveRequest,
    db: Session = Depends(get_db),
):
    item = db.query(CurationSave).filter(CurationSave.id == curation_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Curation not found")

    tracks = payload.tracks or []
    item.name = payload.name
    item.account_id = payload.account_id
    item.tracks = tracks
    item.track_count = len(tracks)
    item.updated_at = datetime.utcnow()

    db.add(item)
    db.commit()
    db.refresh(item)

    return serialize_curation(item)


@router.delete("/api/curations/{curation_id}")
def delete_curation(curation_id: str, db: Session = Depends(get_db)):
    item = db.query(CurationSave).filter(CurationSave.id == curation_id).first()
    if not item:
        return {"message": "Already deleted"}

    db.delete(item)
    db.commit()
    return {"message": "Deleted"}
