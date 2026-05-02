from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base


class SyncGroup(Base):
    __tablename__ = "sync_groups"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("spotify_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    master_playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True, index=True)
    cached_for_quick_scan = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("SpotifyAccount", back_populates="sync_groups")
    master_playlist = relationship("Playlist", foreign_keys=[master_playlist_id])
    children = relationship(
        "SyncGroupChild",
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SyncGroupChild(Base):
    __tablename__ = "sync_group_children"
    __table_args__ = (
        UniqueConstraint("group_id", "playlist_id", name="uq_sync_group_children_group_playlist"),
    )

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("sync_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    group = relationship("SyncGroup", back_populates="children")
    playlist = relationship("Playlist")