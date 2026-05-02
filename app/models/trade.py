from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("spotify_accounts.id", ondelete="CASCADE"), nullable=False, index=True)

    track_name = Column(String, nullable=False)
    artist_name = Column(String, nullable=False)
    playlist_count = Column(Integer, nullable=False, default=0)

    status = Column(String, nullable=False, default="active", index=True)
    is_archived = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow() + timedelta(days=28))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("SpotifyAccount", back_populates="trades")

    placements = relationship(
        "TradePlacement",
        back_populates="trade",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TradePlacement.created_at.asc()",
    )


class TradePlacement(Base):
    __tablename__ = "trade_placements"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True)

    track_name = Column(String, nullable=False)
    artist_name = Column(String, nullable=True)
    display_name = Column(Text, nullable=True)
    spotify_url = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    trade = relationship("Trade", back_populates="placements")