from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, Integer, String, Text, func
from sqlalchemy.orm import Session

from app.core.database import Base, get_db

router = APIRouter(prefix="/api/production/mashups", tags=["production-mashups"])


class ProductionMashup(Base):
    __tablename__ = "production_mashups"

    id = Column(Integer, primary_key=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)

    first_track_id = Column(Integer, nullable=True)
    first_source = Column(String(80), nullable=False, default="")
    first_source_label = Column(String(120), nullable=False, default="")
    first_table_name = Column(String(255), nullable=False, default="")
    first_song = Column(Text, nullable=False, default="")
    first_chords = Column(Text, nullable=False, default="-")
    first_key_signature = Column(String(80), nullable=False, default="-")

    second_track_id = Column(Integer, nullable=True)
    second_source = Column(String(80), nullable=False, default="")
    second_source_label = Column(String(120), nullable=False, default="")
    second_table_name = Column(String(255), nullable=False, default="")
    second_song = Column(Text, nullable=False, default="")
    second_chords = Column(Text, nullable=False, default="-")
    second_key_signature = Column(String(80), nullable=False, default="-")

    done = Column(Boolean, nullable=False, default=False)
    created_at = Column(String(80), nullable=False, default="")


class MashupTrackIn(BaseModel):
    id: int | None = None
    source: str = ""
    sourceLabel: str = ""
    tableName: str = ""
    song: str
    chords: str = "-"
    keySignature: str = "-"


class MashupCreateIn(BaseModel):
    first: MashupTrackIn
    second: MashupTrackIn
    done: bool = False
    createdAt: str = ""


class MashupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sort_order: int
    first_track_id: int | None
    first_source: str
    first_source_label: str
    first_table_name: str
    first_song: str
    first_chords: str
    first_key_signature: str
    second_track_id: int | None
    second_source: str
    second_source_label: str
    second_table_name: str
    second_song: str
    second_chords: str
    second_key_signature: str
    done: bool
    created_at: str


class MashupPatchIn(BaseModel):
    done: bool | None = None


def normalize_text(value: Any, fallback: str = "-") -> str:
    cleaned = str(value or "").strip()
    return cleaned if cleaned else fallback


def mashup_to_out(mashup: ProductionMashup) -> MashupOut:
    return MashupOut.model_validate(mashup)


@router.get("", response_model=list[MashupOut])
@router.get("/", response_model=list[MashupOut])
def list_mashups(db: Session = Depends(get_db)):
    rows = (
        db.query(ProductionMashup)
        .order_by(ProductionMashup.done.asc(), ProductionMashup.sort_order.asc(), ProductionMashup.id.desc())
        .all()
    )
    return [mashup_to_out(row) for row in rows]


@router.post("", response_model=MashupOut)
@router.post("/", response_model=MashupOut)
def create_mashup(payload: MashupCreateIn, db: Session = Depends(get_db)):
    max_sort = db.query(func.max(ProductionMashup.sort_order)).scalar()
    next_sort = int(max_sort or 0) + 1

    first = payload.first
    second = payload.second

    row = ProductionMashup(
        sort_order=next_sort,
        first_track_id=first.id,
        first_source=normalize_text(first.source, ""),
        first_source_label=normalize_text(first.sourceLabel, ""),
        first_table_name=normalize_text(first.tableName, ""),
        first_song=normalize_text(first.song, ""),
        first_chords=normalize_text(first.chords),
        first_key_signature=normalize_text(first.keySignature),
        second_track_id=second.id,
        second_source=normalize_text(second.source, ""),
        second_source_label=normalize_text(second.sourceLabel, ""),
        second_table_name=normalize_text(second.tableName, ""),
        second_song=normalize_text(second.song, ""),
        second_chords=normalize_text(second.chords),
        second_key_signature=normalize_text(second.keySignature),
        done=bool(payload.done),
        created_at=normalize_text(payload.createdAt, ""),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return mashup_to_out(row)


@router.patch("/{mashup_id}", response_model=MashupOut)
def update_mashup(mashup_id: int, payload: MashupPatchIn, db: Session = Depends(get_db)):
    row = db.query(ProductionMashup).filter(ProductionMashup.id == mashup_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Mashup not found.")

    if payload.done is not None:
        row.done = bool(payload.done)

    db.commit()
    db.refresh(row)
    return mashup_to_out(row)


@router.delete("/{mashup_id}")
def delete_mashup(mashup_id: int, db: Session = Depends(get_db)):
    row = db.query(ProductionMashup).filter(ProductionMashup.id == mashup_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Mashup not found.")

    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": mashup_id}
