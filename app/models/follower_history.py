from datetime import date as date_type
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.core.database import Base


class FollowerHistory(Base):
    __tablename__ = "follower_history"

    id = Column(Integer, primary_key=True, index=True)

    playlist_id = Column(
        Integer,
        ForeignKey("playlists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    date = Column(
        Date,
        nullable=False,
        default=date_type.today,
        index=True,
    )

    followers = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    playlist = relationship("Playlist", back_populates="follower_history")