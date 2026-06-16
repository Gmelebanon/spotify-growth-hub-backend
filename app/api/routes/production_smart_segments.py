from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint
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


def _seed_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "production_smart_segments_seed.json"


def _load_seed_data() -> list[dict[str, Any]]:
    path = _seed_path()
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        return []

    return data


def _seed_missing_tables(db: Session) -> None:
    seed_tables = _load_seed_data()

    for table in seed_tables:
        table_name = str(table.get("name", "")).strip()
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
                song=str(row.get("song") or "").strip(),
                key_signature=str(row.get("key_signature") or "-").strip(),
                chords=str(row.get("chords") or "-").strip(),
                tempo=str(row.get("tempo") or "-").strip(),
                genre=str(row.get("genre") or "-").strip(),
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
        grouped.setdefault(row.table_name, []).append(row)

    preferred_order = [table.get("name") for table in _load_seed_data() if table.get("name")]
    remaining_names = sorted(name for name in grouped.keys() if name not in preferred_order)
    ordered_names = [name for name in preferred_order if name in grouped] + remaining_names

    return [{"name": name, "rows": grouped[name]} for name in ordered_names]


@router.patch("/rows/{row_id}", response_model=SmartSegmentRowOut)
def update_smart_segment_row(
    row_id: int,
    payload: SmartSegmentRowPatch,
    db: Session = Depends(get_db),
):
    row = db.get(ProductionSmartSegmentRow, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Smart segment row not found")

    update_data = payload.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field not in EDITABLE_FIELDS:
            continue

        if field in EDITABLE_TEXT_FIELDS:
            setattr(row, field, str(value or "-").strip() or "-")
        else:
            setattr(row, field, bool(value))

    db.add(row)
    db.commit()
    db.refresh(row)

    return row
