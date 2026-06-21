from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, text
from sqlalchemy.orm import Session

from app.core.database import Base, get_db

router = APIRouter(prefix="/api/artist-library", tags=["artist-library"])


class ArtistManualData(Base):
    __tablename__ = "artist_manual_data"

    id = Column(Integer, primary_key=True, index=True)
    artist_id = Column(String(128), nullable=True, index=True)
    name = Column(Text, nullable=False, index=True)
    spotify_url = Column(Text, nullable=True)
    genre = Column(Text, nullable=True)
    popularity = Column(Float, nullable=True)
    streams = Column(Float, nullable=True)
    streams_per_track = Column(Float, nullable=True)
    radio_discover = Column(Float, nullable=True)
    manual_data_updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ManualArtistPayload(BaseModel):
    artistId: str | None = None
    id: str | None = None
    name: str
    spotifyUrl: str | None = None
    genre: str | None = None
    genres: list[str] | None = None
    popularity: float | int | str | None = None
    streams: float | int | str | None = None
    streamsPerTrack: float | int | str | None = None
    streams_per_track: float | int | str | None = None
    radioDiscover: float | int | str | None = None
    radio_discover: float | int | str | None = None
    manualDataUpdatedAt: str | None = None
    manual_data_updated_at: str | None = None


class ManualArtistBatchPayload(BaseModel):
    artists: list[ManualArtistPayload]


def ensure_artist_manual_data_schema(db: Session) -> None:
    bind = db.get_bind()
    ArtistManualData.__table__.create(bind=bind, checkfirst=True)

    # Safe for existing databases where the table may have been created in an older version.
    statements = [
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS artist_id VARCHAR(128)",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS name TEXT",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS spotify_url TEXT",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS genre TEXT",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS popularity DOUBLE PRECISION",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS streams DOUBLE PRECISION",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS streams_per_track DOUBLE PRECISION",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS radio_discover DOUBLE PRECISION",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS manual_data_updated_at TIMESTAMP",
        "ALTER TABLE IF EXISTS artist_manual_data ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS ix_artist_manual_data_artist_id ON artist_manual_data (artist_id)",
    ]

    for statement in statements:
        try:
            db.execute(text(statement))
        except Exception:
            # SQLite/local dev may not support every Postgres IF NOT EXISTS variant.
            db.rollback()

    db.commit()


def parse_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None

    try:
        cleaned = str(value).replace(",", "").replace("%", "").strip()
        if not cleaned:
            return None
        return float(cleaned)
    except Exception:
        return None


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()

    try:
        clean = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(clean)
        return parsed.replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def serialize_manual_row(row: ArtistManualData) -> dict[str, Any]:
    return {
        "id": row.artist_id,
        "artistId": row.artist_id,
        "name": row.name,
        "spotifyUrl": row.spotify_url,
        "genre": row.genre,
        "popularity": row.popularity,
        "streams": row.streams,
        "streamsPerTrack": row.streams_per_track,
        "streams_per_track": row.streams_per_track,
        "radioDiscover": row.radio_discover,
        "radio_discover": row.radio_discover,
        "manualDataUpdatedAt": row.manual_data_updated_at.isoformat() if row.manual_data_updated_at else None,
        "manual_data_updated_at": row.manual_data_updated_at.isoformat() if row.manual_data_updated_at else None,
    }


def upsert_manual_artist(db: Session, payload: ManualArtistPayload) -> ArtistManualData:
    artist_id = payload.artistId or payload.id
    name = str(payload.name or "").strip()
    genre = payload.genre or ((payload.genres or [""])[0] if payload.genres else None)

    query = db.query(ArtistManualData)

    row = None
    if artist_id:
        row = query.filter(ArtistManualData.artist_id == artist_id).first()

    if row is None and name:
        row = query.filter(ArtistManualData.name == name).first()

    if row is None:
        row = ArtistManualData(
            artist_id=artist_id,
            name=name,
            created_at=datetime.utcnow(),
        )
        db.add(row)

    row.artist_id = artist_id or row.artist_id
    row.name = name or row.name
    row.spotify_url = payload.spotifyUrl or row.spotify_url
    row.genre = genre or row.genre
    row.popularity = parse_float(payload.popularity)
    row.streams = parse_float(payload.streams)
    row.streams_per_track = parse_float(payload.streamsPerTrack if payload.streamsPerTrack is not None else payload.streams_per_track)
    row.radio_discover = parse_float(payload.radioDiscover if payload.radioDiscover is not None else payload.radio_discover)
    row.manual_data_updated_at = parse_datetime(payload.manualDataUpdatedAt or payload.manual_data_updated_at)

    return row


@router.get("/manual-data")
def list_manual_artist_data(db: Session = Depends(get_db)):
    ensure_artist_manual_data_schema(db)
    rows = (
        db.query(ArtistManualData)
        .order_by(ArtistManualData.manual_data_updated_at.desc(), ArtistManualData.name.asc())
        .all()
    )

    latest = rows[0].manual_data_updated_at.isoformat() if rows and rows[0].manual_data_updated_at else None

    return {
        "ok": True,
        "latestManualDataUpdatedAt": latest,
        "items": [serialize_manual_row(row) for row in rows],
    }


@router.post("/manual-data")
def save_manual_artist_data(payload: ManualArtistBatchPayload, db: Session = Depends(get_db)):
    ensure_artist_manual_data_schema(db)

    saved = []

    for artist in payload.artists:
        row = upsert_manual_artist(db, artist)
        saved.append(row)

    db.commit()

    return {
        "ok": True,
        "saved": len(saved),
        "items": [serialize_manual_row(row) for row in saved],
    }
