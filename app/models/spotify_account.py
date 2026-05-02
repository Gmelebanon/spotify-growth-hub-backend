from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.core.database import Base


class SpotifyAccount(Base):
    __tablename__ = "spotify_accounts"

    id = Column(Integer, primary_key=True, index=True)
    spotify_user_id = Column(String, unique=True, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    playlists = relationship(
        "Playlist",
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    trades = relationship(
        "Trade",
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    sync_groups = relationship(
        "SyncGroup",
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def expires_at(self):
        return self.token_expires_at

    @expires_at.setter
    def expires_at(self, value):
        self.token_expires_at = value

    @property
    def spotify_id(self):
        return self.spotify_user_id

    @spotify_id.setter
    def spotify_id(self, value):
        self.spotify_user_id = value