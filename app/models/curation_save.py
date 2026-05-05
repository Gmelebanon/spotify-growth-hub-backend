from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String

from app.core.database import Base


class CurationSave(Base):
    __tablename__ = "curation_saves"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    account_id = Column(Integer, nullable=True, index=True)
    track_count = Column(Integer, nullable=False, default=0)
    tracks = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
