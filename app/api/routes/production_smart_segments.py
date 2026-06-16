from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Session

from app.core.database import Base, get_db

router = APIRouter(prefix="/api/production/smart-segments", tags=["production-smart-segments"])

SEGMENT_FIELDS = [
    "afropop",
    "soft_pop",
    "hyper_pop",
    "garage",
    "chill_house",
    "techno",
    "reggae",
    "afro_house",
]

EDITABLE_TEXT_FIELDS = {"song", "key_signature", "chords", "tempo", "genre"}
EDITABLE_FIELDS = EDITABLE_TEXT_FIELDS | set(SEGMENT_FIELDS) | {"row_color", "table_name"}

TABLE_NAME_MAP = {
    "TCC - Spotify Shared - Prod Stems": "Stems",
    "TCC - Spotify Shared - Prod Remakes": "Remakes",
    "TCC - Spotify Shared - Prod Vocals": "Vocals",
    "Production Stems": "Stems",
    "Production Remakes": "Remakes",
    "Production Vocals": "Vocals",
}

DEFAULT_TABLE_ORDER = ["Stems", "Remakes", "Vocals"]
ALLOWED_ROW_COLORS = {"", "green", "yellow", "blue", "purple", "pink", "orange", "red", "gray"}


class ProductionSmartSegmentSheet(Base):
    __tablename__ = "production_smart_segment_sheets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)


class ProductionSmartSegmentRow(Base):
    __tablename__ = "production_smart_segment_rows"
    __table_args__ = (
        UniqueConstraint("table_name", "sort_order", name="uq_production_smart_segment_table_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    table_name = Column(String(255), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False, default=0)

    song = Column(Text, nullable=False, default="")
    key_signature = Column(String(80), nullable=False, default="-")
    chords = Column(Text, nullable=False, default="-")
    tempo = Column(String(80), nullable=False, default="-")
    genre = Column(String(120), nullable=False, default="-")
    row_color = Column(String(40), nullable=False, default="")

    afropop = Column(Boolean, nullable=False, default=False)
    soft_pop = Column(Boolean, nullable=False, default=False)
    hyper_pop = Column(Boolean, nullable=False, default=False)
    garage = Column(Boolean, nullable=False, default=False)
    chill_house = Column(Boolean, nullable=False, default=False)
    techno = Column(Boolean, nullable=False, default=False)
    reggae = Column(Boolean, nullable=False, default=False)
    afro_house = Column(Boolean, nullable=False, default=False)


class SmartSegmentRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    table_name: str
    sort_order: int
    song: str
    key_signature: str
    chords: str
    tempo: str
    genre: str
    row_color: str = ""
    afropop: bool
    soft_pop: bool
    hyper_pop: bool
    garage: bool
    chill_house: bool
    techno: bool
    reggae: bool
    afro_house: bool


class SmartSegmentTableOut(BaseModel):
    name: str
    rows: list[SmartSegmentRowOut]


class SmartSegmentRowPatch(BaseModel):
    table_name: str | None = None
    song: str | None = None
    key_signature: str | None = None
    chords: str | None = None
    tempo: str | None = None
    genre: str | None = None
    row_color: str | None = None
    afropop: bool | None = None
    soft_pop: bool | None = None
    hyper_pop: bool | None = None
    garage: bool | None = None
    chill_house: bool | None = None
    techno: bool | None = None
    reggae: bool | None = None
    afro_house: bool | None = None


class SmartSegmentRowCreate(SmartSegmentRowPatch):
    table_name: str


class SmartSegmentRowsDelete(BaseModel):
    row_ids: list[int]


class SmartSegmentRowsBulkPatch(BaseModel):
    row_ids: list[int]
    table_name: str | None = None
    row_color: str | None = None


class SmartSegmentSheetCreate(BaseModel):
    name: str


class SmartSegmentRowsRestore(BaseModel):
    rows: list[SmartSegmentRowPatch]


def _seed_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "production_smart_segments_seed.json"


def _normalize_table_name(name: str) -> str:
    cleaned_name = str(name or "").strip()
    return TABLE_NAME_MAP.get(cleaned_name, cleaned_name)


def _normalize_text(value: Any, fallback: str = "-") -> str:
    cleaned = str(value if value is not None else "").strip()
    return cleaned or fallback


def _normalize_color(value: Any) -> str:
    cleaned = str(value if value is not None else "").strip().lower()
    return cleaned if cleaned in ALLOWED_ROW_COLORS else ""


def _ensure_schema(db: Session) -> None:
    # Safe for PostgreSQL/Render and harmless after the first run.
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS production_smart_segment_sheets (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    )
    db.execute(
        text(
            "ALTER TABLE production_smart_segment_rows "
            "ADD COLUMN IF NOT EXISTS row_color VARCHAR(40) NOT NULL DEFAULT ''"
        )
    )
    db.commit()


def _load_seed_data() -> list[dict[str, Any]]:
    path = _seed_path()
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    return data if isinstance(data, list) else []


def _get_sheet_names(db: Session) -> list[str]:
    sheet_rows = (
        db.query(ProductionSmartSegmentSheet)
        .order_by(ProductionSmartSegmentSheet.sort_order.asc(), ProductionSmartSegmentSheet.id.asc())
        .all()
    )
    saved_names = [_normalize_table_name(sheet.name) for sheet in sheet_rows]

    row_names = [
        _normalize_table_name(name)
        for (name,) in db.query(ProductionSmartSegmentRow.table_name).distinct().all()
    ]

    names: list[str] = []
    for name in DEFAULT_TABLE_ORDER + saved_names + sorted(row_names):
        if name and name not in names:
            names.append(name)
    return names


def _ensure_sheet(db: Session, name: str) -> None:
    table_name = _normalize_table_name(name)
    if not table_name:
        return

    existing = (
        db.query(ProductionSmartSegmentSheet)
        .filter(ProductionSmartSegmentSheet.name == table_name)
        .first()
    )
    if existing:
        return

    next_sort_order = (
        db.query(func.coalesce(func.max(ProductionSmartSegmentSheet.sort_order), -1)).scalar() + 1
    )
    db.add(ProductionSmartSegmentSheet(name=table_name, sort_order=next_sort_order))


def _rename_legacy_tables(db: Session) -> None:
    changed = False

    for old_name, new_name in TABLE_NAME_MAP.items():
        old_rows = (
            db.query(ProductionSmartSegmentRow)
            .filter(ProductionSmartSegmentRow.table_name == old_name)
            .all()
        )

        if not old_rows:
            continue

        new_count = (
            db.query(ProductionSmartSegmentRow)
            .filter(ProductionSmartSegmentRow.table_name == new_name)
            .count()
        )

        if new_count > 0:
            continue

        for row in old_rows:
            row.table_name = new_name
            db.add(row)
            changed = True

    if changed:
        db.commit()


def _seed_missing_tables(db: Session) -> None:
    _ensure_schema(db)
    _rename_legacy_tables(db)

    for index, table_name in enumerate(DEFAULT_TABLE_ORDER):
        existing = (
            db.query(ProductionSmartSegmentSheet)
            .filter(ProductionSmartSegmentSheet.name == table_name)
            .first()
        )
        if not existing:
            db.add(ProductionSmartSegmentSheet(name=table_name, sort_order=index))

    seed_tables = _load_seed_data()

    for table in seed_tables:
        table_name = _normalize_table_name(str(table.get("name", "")).strip())
        rows = table.get("rows", [])

        if not table_name or not isinstance(rows, list):
            continue

        _ensure_sheet(db, table_name)

        existing_count = (
            db.query(ProductionSmartSegmentRow)
            .filter(ProductionSmartSegmentRow.table_name == table_name)
            .count()
        )

        if existing_count > 0:
            continue

        for index, row in enumerate(rows):
            record = ProductionSmartSegmentRow(
                table_name=table_name,
                sort_order=index,
                song=_normalize_text(row.get("song"), ""),
                key_signature=_normalize_text(row.get("key_signature")),
                chords=_normalize_text(row.get("chords")),
                tempo=_normalize_text(row.get("tempo")),
                genre=_normalize_text(row.get("genre")),
                row_color=_normalize_color(row.get("row_color", "")),
                afropop=bool(row.get("afropop", False)),
                soft_pop=bool(row.get("soft_pop", False)),
                hyper_pop=bool(row.get("hyper_pop", False)),
                garage=bool(row.get("garage", False)),
                chill_house=bool(row.get("chill_house", False)),
                techno=bool(row.get("techno", False)),
                reggae=bool(row.get("reggae", False)),
                afro_house=bool(row.get("afro_house", False)),
            )
            db.add(record)

    db.commit()


def _next_sort_order(db: Session, table_name: str) -> int:
    return (
        db.query(func.coalesce(func.max(ProductionSmartSegmentRow.sort_order), -1))
        .filter(ProductionSmartSegmentRow.table_name == table_name)
        .scalar()
        + 1
    )


def _apply_payload_to_row(row: ProductionSmartSegmentRow, payload_data: dict[str, Any], db: Session | None = None) -> None:
    for field, value in payload_data.items():
        if field not in EDITABLE_FIELDS:
            continue

        if field == "table_name":
            table_name = _normalize_table_name(str(value or ""))
            if not table_name:
                continue
            if db is not None:
                _ensure_sheet(db, table_name)
                if row.table_name != table_name:
                    row.sort_order = _next_sort_order(db, table_name)
            row.table_name = table_name
        elif field == "row_color":
            row.row_color = _normalize_color(value)
        elif field in EDITABLE_TEXT_FIELDS:
            fallback = "" if field == "song" else "-"
            setattr(row, field, _normalize_text(value, fallback))
        else:
            setattr(row, field, bool(value))


@router.get("", response_model=list[SmartSegmentTableOut])
def get_smart_segments(db: Session = Depends(get_db)):
    _seed_missing_tables(db)

    rows = (
        db.query(ProductionSmartSegmentRow)
        .order_by(
            ProductionSmartSegmentRow.table_name.asc(),
            ProductionSmartSegmentRow.sort_order.asc(),
            ProductionSmartSegmentRow.id.asc(),
        )
        .all()
    )

    grouped: dict[str, list[ProductionSmartSegmentRow]] = {name: [] for name in _get_sheet_names(db)}
    for row in rows:
        row.table_name = _normalize_table_name(row.table_name)
        row.row_color = _normalize_color(getattr(row, "row_color", ""))
        grouped.setdefault(row.table_name, []).append(row)

    return [{"name": name, "rows": grouped[name]} for name in grouped.keys()]


@router.post("/sheets", response_model=SmartSegmentTableOut)
def create_smart_segment_sheet(payload: SmartSegmentSheetCreate, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    table_name = _normalize_table_name(payload.name)

    if not table_name:
        raise HTTPException(status_code=400, detail="Sheet name is required")

    existing_names = set(_get_sheet_names(db))
    if table_name in existing_names:
        raise HTTPException(status_code=409, detail="Sheet already exists")

    next_sort_order = (
        db.query(func.coalesce(func.max(ProductionSmartSegmentSheet.sort_order), -1)).scalar() + 1
    )
    sheet = ProductionSmartSegmentSheet(name=table_name, sort_order=next_sort_order)
    db.add(sheet)
    db.commit()

    return {"name": table_name, "rows": []}


@router.delete("/sheets/{sheet_name}")
def delete_smart_segment_sheet(sheet_name: str, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    table_name = _normalize_table_name(unquote(sheet_name))

    if table_name in DEFAULT_TABLE_ORDER:
        raise HTTPException(status_code=400, detail="Default sheets cannot be removed")

    rows_count = (
        db.query(ProductionSmartSegmentRow)
        .filter(ProductionSmartSegmentRow.table_name == table_name)
        .count()
    )
    if rows_count > 0:
        raise HTTPException(status_code=400, detail="Sheet is not empty")

    sheet = (
        db.query(ProductionSmartSegmentSheet)
        .filter(ProductionSmartSegmentSheet.name == table_name)
        .first()
    )
    if sheet:
        db.delete(sheet)
        db.commit()

    return {"deleted": True}


@router.post("/rows", response_model=SmartSegmentRowOut)
def create_smart_segment_row(payload: SmartSegmentRowCreate, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    table_name = _normalize_table_name(payload.table_name)

    if not table_name:
        raise HTTPException(status_code=400, detail="Invalid smart segment table name")

    _ensure_sheet(db, table_name)
    next_sort_order = _next_sort_order(db, table_name)

    row = ProductionSmartSegmentRow(
        table_name=table_name,
        sort_order=next_sort_order,
        song="",
        key_signature="-",
        chords="-",
        tempo="-",
        genre="-",
        row_color="",
        afropop=False,
        soft_pop=False,
        hyper_pop=False,
        garage=False,
        chill_house=False,
        techno=False,
        reggae=False,
        afro_house=False,
    )

    _apply_payload_to_row(row, payload.model_dump(exclude_unset=True, exclude={"table_name"}), db)

    db.add(row)
    db.commit()
    db.refresh(row)

    return row


@router.post("/rows/restore", response_model=list[SmartSegmentRowOut])
def restore_smart_segment_rows(payload: SmartSegmentRowsRestore, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    restored_rows: list[ProductionSmartSegmentRow] = []

    for item in payload.rows:
        data = item.model_dump(exclude_unset=True)
        table_name = _normalize_table_name(str(data.get("table_name") or "Stems"))
        _ensure_sheet(db, table_name)

        row = ProductionSmartSegmentRow(
            table_name=table_name,
            sort_order=_next_sort_order(db, table_name),
            song="",
            key_signature="-",
            chords="-",
            tempo="-",
            genre="-",
            row_color="",
            afropop=False,
            soft_pop=False,
            hyper_pop=False,
            garage=False,
            chill_house=False,
            techno=False,
            reggae=False,
            afro_house=False,
        )
        _apply_payload_to_row(row, data, db)
        db.add(row)
        restored_rows.append(row)

    db.commit()

    for row in restored_rows:
        db.refresh(row)

    return restored_rows


@router.patch("/rows/bulk", response_model=list[SmartSegmentRowOut])
def bulk_update_smart_segment_rows(payload: SmartSegmentRowsBulkPatch, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    unique_row_ids = sorted({row_id for row_id in payload.row_ids if row_id > 0})

    if not unique_row_ids:
        return []

    rows = (
        db.query(ProductionSmartSegmentRow)
        .filter(ProductionSmartSegmentRow.id.in_(unique_row_ids))
        .order_by(ProductionSmartSegmentRow.sort_order.asc(), ProductionSmartSegmentRow.id.asc())
        .all()
    )

    data = payload.model_dump(exclude_unset=True, exclude={"row_ids"})
    target_table = data.get("table_name")
    next_order = None

    if target_table is not None:
        target_table = _normalize_table_name(target_table)
        if not target_table:
            raise HTTPException(status_code=400, detail="Invalid target sheet")
        _ensure_sheet(db, target_table)
        next_order = _next_sort_order(db, target_table)

    for row in rows:
        if target_table is not None:
            row.table_name = target_table
            row.sort_order = next_order or 0
            next_order = (next_order or 0) + 1
        if "row_color" in data:
            row.row_color = _normalize_color(data.get("row_color"))
        db.add(row)

    db.commit()

    for row in rows:
        db.refresh(row)

    return rows


@router.patch("/rows/{row_id}", response_model=SmartSegmentRowOut)
def update_smart_segment_row(row_id: int, payload: SmartSegmentRowPatch, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    row = db.get(ProductionSmartSegmentRow, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Smart segment row not found")

    _apply_payload_to_row(row, payload.model_dump(exclude_unset=True), db)

    db.add(row)
    db.commit()
    db.refresh(row)

    return row


@router.delete("/rows")
def delete_smart_segment_rows(payload: SmartSegmentRowsDelete, db: Session = Depends(get_db)):
    _seed_missing_tables(db)
    unique_row_ids = sorted({row_id for row_id in payload.row_ids if row_id > 0})

    if not unique_row_ids:
        return {"deleted_count": 0}

    rows = (
        db.query(ProductionSmartSegmentRow)
        .filter(ProductionSmartSegmentRow.id.in_(unique_row_ids))
        .all()
    )

    deleted_count = len(rows)

    for row in rows:
        db.delete(row)

    db.commit()

    return {"deleted_count": deleted_count}
