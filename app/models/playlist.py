from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship, synonym

from app.core.database import Base


class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True, index=True)

    account_id = Column(
        Integer,
        ForeignKey("spotify_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    spotify_playlist_id = Column(String, nullable=True, index=True)
    spotify_id = Column(String, nullable=True, index=True)

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    image_url = Column(String, nullable=True)
    spotify_url = Column(Text, nullable=True)
    external_url = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    playlist_url = Column(Text, nullable=True)
    external_id = Column(String, nullable=True)

    owner_id = Column(Integer, nullable=True)
    owner_display_name = Column(String, nullable=True)
    owner_name = Column(String, nullable=True)

    followers = Column(Integer, nullable=False, default=0)

    tracks_total = Column(Integer, nullable=False, default=0)
    tracks_count = Column(Integer, nullable=False, default=0)

    genre = Column(String, nullable=True)

    public = Column(Boolean, nullable=True, default=True)

    # Used by the sync job to hide Spotify playlists that were deleted,
    # removed from the account, or are no longer available from Spotify.
    # We keep the database row and follower history instead of deleting it.
    is_active = Column(Boolean, nullable=True, default=True, index=True)
    is_available = Column(Boolean, nullable=True, default=True, index=True)
    unavailable_since = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    account = relationship(
        "SpotifyAccount",
        back_populates="playlists",
    )

    follower_history = relationship(
        "FollowerHistory",
        back_populates="playlist",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="FollowerHistory.created_at.asc()",
    )

    @property
    def resolved_spotify_id(self):
        return self.spotify_id or self.spotify_playlist_id

    @property
    def resolved_owner_name(self):
        return self.owner_name or self.owner_display_name

    @property
    def resolved_tracks_count(self):
        if (self.tracks_count or 0) > 0:
            return self.tracks_count

        return self.tracks_total or 0

    @property
    def playlist_spotify_id_raw(self):
        return self.spotify_playlist_id

    @playlist_spotify_id_raw.setter
    def playlist_spotify_id_raw(self, value):
        self.spotify_playlist_id = value

    @property
    def total_tracks(self):
        return self.resolved_tracks_count

    @total_tracks.setter
    def total_tracks(self, value):
        self.tracks_total = value

    @property
    def track_count(self):
        return self.resolved_tracks_count

    @track_count.setter
    def track_count(self, value):
        self.tracks_count = value

    spotify_playlist_id_legacy = synonym("spotify_playlist_id")


class PlaylistManagerState(Base):
    __tablename__ = "playlist_manager_state"

    id = Column(Integer, primary_key=True, index=True)

    master_playlist = Column(JSON, nullable=True)

    synced_playlists = Column(JSON, nullable=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )