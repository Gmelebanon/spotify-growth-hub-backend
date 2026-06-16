from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint, func
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
EDITABLE_FIELDS = EDITABLE_TEXT_FIELDS | set(SEGMENT_FIELDS)

TABLE_NAME_MAP = {
    "TCC - Spotify Shared - Prod Stems": "Stems",
    "TCC - Spotify Shared - Prod Remakes": "Remakes",
    "TCC - Spotify Shared - Prod Vocals": "Vocals",
    "Production Stems": "Stems",
    "Production Remakes": "Remakes",
    "Production Vocals": "Vocals",
}

TABLE_ORDER = ["Stems", "Remakes", "Vocals"]


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
    song: str | None = None
    key_signature: str | None = None
    chords: str | None = None
    tempo: str | None = None
    genre: str | None = None
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


def _seed_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "production_smart_segments_seed.json"


def _normalize_table_name(name: str) -> str:
    cleaned_name = str(name or "").strip()
    return TABLE_NAME_MAP.get(cleaned_name, cleaned_name)


def _normalize_text(value: Any, fallback: str = "-") -> str:
    cleaned = str(value if value is not None else "").strip()
    return cleaned or fallback


def _load_seed_data() -> list[dict[str, Any]]:
    path = _seed_path()
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        return []

    return data


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
    _rename_legacy_tables(db)

    seed_tables = _load_seed_data()

    for table in seed_tables:
        table_name = _normalize_table_name(str(table.get("name", "")).strip())
        rows = table.get("rows", [])

        if not table_name or not isinstance(rows, list):
            continue

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


def _apply_payload_to_row(row: ProductionSmartSegmentRow, payload_data: dict[str, Any]) -> None:
    for field, value in payload_data.items():
        if field not in EDITABLE_FIELDS:
            continue

        if field in EDITABLE_TEXT_FIELDS:
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

    grouped: dict[str, list[ProductionSmartSegmentRow]] = {}
    for row in rows:
        row.table_name = _normalize_table_name(row.table_name)
        grouped.setdefault(row.table_name, []).append(row)

    remaining_names = sorted(name for name in grouped.keys() if name not in TABLE_ORDER)
    ordered_names = [name for name in TABLE_ORDER if name in grouped] + remaining_names

    return [{"name": name, "rows": grouped[name]} for name in ordered_names]


@router.post("/rows", response_model=SmartSegmentRowOut)
def create_smart_segment_row(
    payload: SmartSegmentRowCreate,
    db: Session = Depends(get_db),
):
    table_name = _normalize_table_name(payload.table_name)

    if table_name not in TABLE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid smart segment table name")

    next_sort_order = (
        db.query(func.coalesce(func.max(ProductionSmartSegmentRow.sort_order), -1))
        .filter(ProductionSmartSegmentRow.table_name == table_name)
        .scalar()
        + 1
    )

    row = ProductionSmartSegmentRow(
        table_name=table_name,
        sort_order=next_sort_order,
        song="",
        key_signature="-",
        chords="-",
        tempo="-",
        genre="-",
        afropop=False,
        soft_pop=False,
        hyper_pop=False,
        garage=False,
        chill_house=False,
        techno=False,
        reggae=False,
        afro_house=False,
    )

    _apply_payload_to_row(row, payload.model_dump(exclude_unset=True, exclude={"table_name"}))

    db.add(row)
    db.commit()
    db.refresh(row)

    return row


@router.patch("/rows/{row_id}", response_model=SmartSegmentRowOut)
def update_smart_segment_row(
    row_id: int,
    payload: SmartSegmentRowPatch,
    db: Session = Depends(get_db),
):
    row = db.get(ProductionSmartSegmentRow, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Smart segment row not found")

    _apply_payload_to_row(row, payload.model_dump(exclude_unset=True))

    db.add(row)
    db.commit()
    db.refresh(row)

    return row


@router.delete("/rows")
def delete_smart_segment_rows(
    payload: SmartSegmentRowsDelete,
    db: Session = Depends(get_db),
):
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
