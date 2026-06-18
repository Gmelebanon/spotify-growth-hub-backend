from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, Integer, String, Text, func
from sqlalchemy.orm import Session

from app.core.database import Base, get_db

router = APIRouter(prefix="/api/scheduling", tags=["scheduling"])

EDITABLE_TEXT_FIELDS = {
    "genre",
    "status",
    "artist",
    "album",
    "song",
    "release_date",
    "platform_status",
    "rn_account",
    "remarks",
}
EDITABLE_FIELDS = EDITABLE_TEXT_FIELDS | {"is_selected"}

STATUS_OPTIONS = [
    "In Progress",
    "Online",
    "Rejected",
    "Scheduled",
    "Approved",
    "No Artist",
    "-",
    "",
]


class SchedulingRow(Base):
    __tablename__ = "scheduling_rows"

    id = Column(Integer, primary_key=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_selected = Column(Boolean, nullable=False, default=False)

    genre = Column(String(120), nullable=False, default="")
    status = Column(String(80), nullable=False, default="")
    artist = Column(Text, nullable=False, default="")
    album = Column(Text, nullable=False, default="")
    song = Column(Text, nullable=False, default="")
    release_date = Column(String(80), nullable=False, default="")
    platform_status = Column(String(80), nullable=False, default="")
    rn_account = Column(String(120), nullable=False, default="")
    remarks = Column(Text, nullable=False, default="")


class SchedulingRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sort_order: int
    is_selected: bool = False
    genre: str = ""
    status: str = ""
    artist: str = ""
    album: str = ""
    song: str = ""
    release_date: str = ""
    platform_status: str = ""
    rn_account: str = ""
    remarks: str = ""


class SchedulingRowCreate(BaseModel):
    is_selected: bool = False
    genre: str = ""
    status: str = ""
    artist: str = ""
    album: str = ""
    song: str = ""
    release_date: str = ""
    platform_status: str = ""
    rn_account: str = ""
    remarks: str = ""


class SchedulingRowPatch(BaseModel):
    field: str
    value: Any


class SchedulingBulkDelete(BaseModel):
    ids: list[int]


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_seed_rows() -> list[dict[str, Any]]:
    seed_path = Path(__file__).resolve().parents[2] / "data" / "scheduling_seed.json"
    if not seed_path.exists():
        return []
    try:
        return json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def ensure_scheduling_seeded(db: Session) -> None:
    existing_count = db.query(func.count(SchedulingRow.id)).scalar() or 0
    if existing_count > 0:
        return

    rows = []
    for index, item in enumerate(load_seed_rows()):
        rows.append(
            SchedulingRow(
                sort_order=int(item.get("sort_order", index)),
                is_selected=bool(item.get("is_selected", False)),
                genre=normalize_text(item.get("genre", "")),
                status=normalize_text(item.get("status", "")),
                artist=normalize_text(item.get("artist", "")),
                album=normalize_text(item.get("album", "")),
                song=normalize_text(item.get("song", "")),
                release_date=normalize_text(item.get("release_date", "")),
                platform_status=normalize_text(item.get("platform_status", "")),
                rn_account=normalize_text(item.get("rn_account", "")),
                remarks=normalize_text(item.get("remarks", "")),
            )
        )

    if rows:
        db.add_all(rows)
        db.commit()


@router.get("", response_model=list[SchedulingRowOut])
@router.get("/", response_model=list[SchedulingRowOut])
def list_scheduling_rows(db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)
    rows = db.query(SchedulingRow).order_by(SchedulingRow.sort_order.asc(), SchedulingRow.id.asc()).all()
    return [SchedulingRowOut.model_validate(row) for row in rows]


@router.post("", response_model=SchedulingRowOut)
@router.post("/", response_model=SchedulingRowOut)
def create_scheduling_row(payload: SchedulingRowCreate, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    max_sort = db.query(func.max(SchedulingRow.sort_order)).scalar()
    next_sort = int(max_sort or 0) + 1

    row = SchedulingRow(
        sort_order=next_sort,
        is_selected=payload.is_selected,
        genre=normalize_text(payload.genre),
        status=normalize_text(payload.status),
        artist=normalize_text(payload.artist),
        album=normalize_text(payload.album),
        song=normalize_text(payload.song),
        release_date=normalize_text(payload.release_date),
        platform_status=normalize_text(payload.platform_status),
        rn_account=normalize_text(payload.rn_account),
        remarks=normalize_text(payload.remarks),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return SchedulingRowOut.model_validate(row)


@router.patch("/{row_id}", response_model=SchedulingRowOut)
def update_scheduling_row(row_id: int, payload: SchedulingRowPatch, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    row = db.query(SchedulingRow).filter(SchedulingRow.id == row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduling row not found.")

    if payload.field not in EDITABLE_FIELDS:
        raise HTTPException(status_code=400, detail="Field cannot be edited.")

    if payload.field == "is_selected":
        setattr(row, payload.field, bool(payload.value))
    else:
        setattr(row, payload.field, normalize_text(payload.value))

    db.commit()
    db.refresh(row)
    return SchedulingRowOut.model_validate(row)


@router.delete("/{row_id}")
def delete_scheduling_row(row_id: int, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    row = db.query(SchedulingRow).filter(SchedulingRow.id == row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduling row not found.")

    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": row_id}


@router.post("/bulk-delete")
def bulk_delete_scheduling_rows(payload: SchedulingBulkDelete, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    ids = list(dict.fromkeys(payload.ids))
    if not ids:
        return {"ok": True, "deleted_ids": []}

    rows = db.query(SchedulingRow).filter(SchedulingRow.id.in_(ids)).all()
    deleted_ids = [row.id for row in rows]
    for row in rows:
        db.delete(row)

    db.commit()
    return {"ok": True, "deleted_ids": deleted_ids}
