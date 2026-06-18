from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, Integer, String, Text, func, text
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

DEFAULT_SHEET_NAME = "Schedule"

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


class SchedulingSheet(Base):
    __tablename__ = "scheduling_sheets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)


class SchedulingRow(Base):
    __tablename__ = "scheduling_rows"

    id = Column(Integer, primary_key=True, index=True)
    sheet_id = Column(Integer, nullable=True, index=True)
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


class SchedulingSheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int


class SchedulingRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sheet_id: int | None = None
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


class SchedulingPayload(BaseModel):
    sheets: list[SchedulingSheetOut]
    rows: list[SchedulingRowOut]


class SchedulingSheetCreate(BaseModel):
    name: str


class SchedulingSheetRename(BaseModel):
    name: str


class SchedulingRowCreate(BaseModel):
    sheet_id: int | None = None
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



def ensure_scheduling_schema(db: Session) -> None:
    """Create missing columns for older databases.

    SQLAlchemy create_all creates new tables but does not alter existing tables.
    This keeps existing Supabase data while adding sheet support.
    """
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS scheduling_sheets (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    )

    db.execute(
        text(
            """
            ALTER TABLE IF EXISTS scheduling_rows
            ADD COLUMN IF NOT EXISTS sheet_id INTEGER
            """
        )
    )

    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_scheduling_rows_sheet_id
            ON scheduling_rows (sheet_id)
            """
        )
    )

    db.commit()


def get_or_create_default_sheet(db: Session) -> SchedulingSheet:
    sheet = (
        db.query(SchedulingSheet)
        .filter(SchedulingSheet.name == DEFAULT_SHEET_NAME)
        .first()
    )
    if sheet is not None:
        return sheet

    max_sort = db.query(func.max(SchedulingSheet.sort_order)).scalar()
    sheet = SchedulingSheet(
        name=DEFAULT_SHEET_NAME,
        sort_order=int(max_sort or 0),
    )
    db.add(sheet)
    db.commit()
    db.refresh(sheet)
    return sheet


def get_first_sheet(db: Session) -> Optional[SchedulingSheet]:
    return (
        db.query(SchedulingSheet)
        .order_by(SchedulingSheet.sort_order.asc(), SchedulingSheet.id.asc())
        .first()
    )


def ensure_scheduling_seeded(db: Session) -> None:
    ensure_scheduling_schema(db)

    existing_sheet = get_first_sheet(db)
    existing_count = db.query(func.count(SchedulingRow.id)).scalar() or 0

    # Only create the original Schedule sheet when the database is completely empty.
    # This lets users delete the Schedule sheet after creating their own sheets.
    if existing_sheet is None:
        default_sheet = get_or_create_default_sheet(db)
    else:
        default_sheet = existing_sheet

    # Migrate old rows that existed before sheets were added.
    db.query(SchedulingRow).filter(SchedulingRow.sheet_id.is_(None)).update(
        {SchedulingRow.sheet_id: default_sheet.id},
        synchronize_session=False,
    )
    db.commit()

    if existing_count > 0:
        return

    # Seed the table only for a fresh database with no rows.
    rows = []
    for index, item in enumerate(load_seed_rows()):
        rows.append(
            SchedulingRow(
                sheet_id=default_sheet.id,
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


def get_sheet_or_404(sheet_id: int, db: Session) -> SchedulingSheet:
    sheet = db.query(SchedulingSheet).filter(SchedulingSheet.id == sheet_id).first()
    if sheet is None:
        raise HTTPException(status_code=404, detail="Scheduling sheet not found.")
    return sheet


@router.get("", response_model=SchedulingPayload)
@router.get("/", response_model=SchedulingPayload)
def list_scheduling(db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)
    sheets = db.query(SchedulingSheet).order_by(SchedulingSheet.sort_order.asc(), SchedulingSheet.id.asc()).all()
    rows = db.query(SchedulingRow).order_by(SchedulingRow.sort_order.asc(), SchedulingRow.id.asc()).all()
    return SchedulingPayload(
        sheets=[SchedulingSheetOut.model_validate(sheet) for sheet in sheets],
        rows=[SchedulingRowOut.model_validate(row) for row in rows],
    )


@router.post("/sheets", response_model=SchedulingSheetOut)
def create_sheet(payload: SchedulingSheetCreate, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    name = normalize_text(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="Sheet name is required.")

    existing = db.query(SchedulingSheet).filter(func.lower(SchedulingSheet.name) == name.lower()).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Sheet name already exists.")

    max_sort = db.query(func.max(SchedulingSheet.sort_order)).scalar()
    sheet = SchedulingSheet(name=name, sort_order=int(max_sort or 0) + 1)
    db.add(sheet)
    db.commit()
    db.refresh(sheet)
    return SchedulingSheetOut.model_validate(sheet)


@router.patch("/sheets/{sheet_id}", response_model=SchedulingSheetOut)
@router.put("/sheets/{sheet_id}", response_model=SchedulingSheetOut)
@router.post("/sheets/{sheet_id}/rename", response_model=SchedulingSheetOut)
def rename_sheet(sheet_id: int, payload: SchedulingSheetRename, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    sheet = get_sheet_or_404(sheet_id, db)
    name = normalize_text(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="Sheet name is required.")

    duplicate = (
        db.query(SchedulingSheet)
        .filter(func.lower(SchedulingSheet.name) == name.lower(), SchedulingSheet.id != sheet_id)
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Sheet name already exists.")

    sheet.name = name
    db.commit()
    db.refresh(sheet)
    return SchedulingSheetOut.model_validate(sheet)



@router.delete("/sheets/{sheet_id}")
def delete_sheet(sheet_id: int, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    sheet = get_sheet_or_404(sheet_id, db)

    rows = db.query(SchedulingRow).filter(SchedulingRow.sheet_id == sheet_id).all()
    for row in rows:
        db.delete(row)

    db.delete(sheet)
    db.commit()
    return {"ok": True, "deleted_sheet_id": sheet_id, "deleted_rows": len(rows)}


@router.post("/rows", response_model=SchedulingRowOut)
@router.post("", response_model=SchedulingRowOut)
@router.post("/", response_model=SchedulingRowOut)
def create_scheduling_row(payload: SchedulingRowCreate, db: Session = Depends(get_db)):
    ensure_scheduling_seeded(db)

    sheet_id = payload.sheet_id or get_or_create_default_sheet(db).id
    get_sheet_or_404(sheet_id, db)

    max_sort = db.query(func.max(SchedulingRow.sort_order)).filter(SchedulingRow.sheet_id == sheet_id).scalar()
    next_sort = int(max_sort or 0) + 1

    row = SchedulingRow(
        sheet_id=sheet_id,
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


@router.patch("/rows/{row_id}", response_model=SchedulingRowOut)
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


@router.delete("/rows/{row_id}")
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
