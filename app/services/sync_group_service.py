from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models.playlist import Playlist
from app.models.spotify_account import SpotifyAccount
from app.models.sync_group import SyncGroup, SyncGroupChild


class SyncGroupService:
    @staticmethod
    def _get_account_or_raise(db: Session, account_id: int) -> SpotifyAccount:
        account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()
        if not account:
            raise ValueError("Spotify account not found")
        return account

    @staticmethod
    def _get_playlist_or_raise(db: Session, playlist_id: int) -> Playlist:
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if not playlist:
            raise ValueError("Playlist not found")
        return playlist

    @staticmethod
    def _get_group_or_raise(db: Session, group_id: int) -> SyncGroup:
        group = (
            db.query(SyncGroup)
            .options(
                joinedload(SyncGroup.master_playlist),
                joinedload(SyncGroup.children).joinedload(SyncGroupChild.playlist),
            )
            .filter(SyncGroup.id == group_id)
            .first()
        )
        if not group:
            raise ValueError("Sync group not found")
        return group

    @staticmethod
    def _serialize_playlist(playlist: Playlist | None) -> dict[str, Any] | None:
        if not playlist:
            return None

        return {
            "id": playlist.id,
            "spotify_id": getattr(playlist, "spotify_id", None),
            "name": getattr(playlist, "name", None),
            "description": getattr(playlist, "description", None),
            "image_url": getattr(playlist, "image_url", None),
            "owner_name": getattr(playlist, "owner_name", None),
            "followers": getattr(playlist, "followers", 0),
            "tracks_count": getattr(playlist, "tracks_count", 0),
        }

    @classmethod
    def serialize_group(cls, group: SyncGroup) -> dict[str, Any]:
        children = []
        for child in sorted(group.children, key=lambda item: item.created_at or datetime.min):
            if not child.playlist:
                continue

            children.append(
                {
                    "id": child.id,
                    "playlist": cls._serialize_playlist(child.playlist),
                    "created_at": child.created_at.isoformat() if child.created_at else None,
                }
            )

        return {
            "id": group.id,
            "account_id": group.account_id,
            "name": group.name,
            "cached_for_quick_scan": group.cached_for_quick_scan,
            "master_playlist": cls._serialize_playlist(group.master_playlist),
            "children": children,
            "children_count": len(children),
            "created_at": group.created_at.isoformat() if group.created_at else None,
            "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        }

    @classmethod
    def list_groups(cls, db: Session, account_id: int) -> list[dict[str, Any]]:
        cls._get_account_or_raise(db, account_id)

        groups = (
            db.query(SyncGroup)
            .options(
                joinedload(SyncGroup.master_playlist),
                joinedload(SyncGroup.children).joinedload(SyncGroupChild.playlist),
            )
            .filter(SyncGroup.account_id == account_id)
            .order_by(SyncGroup.created_at.desc())
            .all()
        )

        return [cls.serialize_group(group) for group in groups]

    @classmethod
    def create_group(
        cls,
        db: Session,
        account_id: int,
        name: str,
        master_playlist_id: int | None = None,
        child_playlist_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        cls._get_account_or_raise(db, account_id)

        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Group name is required")

        master_playlist = None
        if master_playlist_id is not None:
            master_playlist = cls._get_playlist_or_raise(db, master_playlist_id)
            if master_playlist.account_id != account_id:
                raise ValueError("Master playlist does not belong to this account")

        group = SyncGroup(
            account_id=account_id,
            name=clean_name,
            master_playlist_id=master_playlist_id,
            cached_for_quick_scan=False,
        )

        db.add(group)
        db.flush()

        for playlist_id in child_playlist_ids or []:
            playlist = cls._get_playlist_or_raise(db, playlist_id)
            if playlist.account_id != account_id:
                raise ValueError(f"Playlist {playlist_id} does not belong to this account")

            existing = (
                db.query(SyncGroupChild)
                .filter(
                    SyncGroupChild.group_id == group.id,
                    SyncGroupChild.playlist_id == playlist_id,
                )
                .first()
            )
            if not existing:
                db.add(SyncGroupChild(group_id=group.id, playlist_id=playlist_id))

        db.commit()
        db.refresh(group)

        return cls.serialize_group(cls._get_group_or_raise(db, group.id))

    @classmethod
    def add_child_playlist(cls, db: Session, group_id: int, playlist_id: int) -> dict[str, Any]:
        group = cls._get_group_or_raise(db, group_id)
        playlist = cls._get_playlist_or_raise(db, playlist_id)

        if playlist.account_id != group.account_id:
            raise ValueError("Playlist does not belong to the same account as this sync group")

        existing = (
            db.query(SyncGroupChild)
            .filter(
                SyncGroupChild.group_id == group_id,
                SyncGroupChild.playlist_id == playlist_id,
            )
            .first()
        )
        if existing:
            raise ValueError("Playlist is already attached to this sync group")

        child = SyncGroupChild(group_id=group_id, playlist_id=playlist_id)
        db.add(child)
        db.commit()

        return cls.serialize_group(cls._get_group_or_raise(db, group_id))

    @classmethod
    def remove_child_playlist(cls, db: Session, group_id: int, child_id: int) -> dict[str, Any]:
        cls._get_group_or_raise(db, group_id)

        child = (
            db.query(SyncGroupChild)
            .filter(
                SyncGroupChild.id == child_id,
                SyncGroupChild.group_id == group_id,
            )
            .first()
        )
        if not child:
            raise ValueError("Child playlist link not found")

        db.delete(child)
        db.commit()

        return cls.serialize_group(cls._get_group_or_raise(db, group_id))

    @classmethod
    def set_cached_for_quick_scan(cls, db: Session, group_id: int, enabled: bool = True) -> dict[str, Any]:
        group = cls._get_group_or_raise(db, group_id)
        group.cached_for_quick_scan = enabled
        group.updated_at = datetime.utcnow()

        db.add(group)
        db.commit()
        db.refresh(group)

        return cls.serialize_group(cls._get_group_or_raise(db, group_id))

    @classmethod
    def sync_group(cls, db: Session, group_id: int) -> dict[str, Any]:
        group = cls._get_group_or_raise(db, group_id)
        group.updated_at = datetime.utcnow()

        db.add(group)
        db.commit()
        db.refresh(group)

        return {
            "message": "Sync started",
            "group": cls.serialize_group(cls._get_group_or_raise(db, group_id)),
        }

    @classmethod
    def add_one_track(cls, db: Session, group_id: int, track_name: str, artist_name: str | None = None) -> dict[str, Any]:
        group = cls._get_group_or_raise(db, group_id)

        clean_track_name = (track_name or "").strip()
        clean_artist_name = (artist_name or "").strip() if artist_name else None

        if not clean_track_name:
            raise ValueError("Track name is required")

        group.updated_at = datetime.utcnow()
        db.add(group)
        db.commit()
        db.refresh(group)

        return {
            "message": "Track queued for sync group",
            "track": {
                "name": clean_track_name,
                "artist_name": clean_artist_name,
            },
            "group": cls.serialize_group(cls._get_group_or_raise(db, group_id)),
        }

    @classmethod
    def delete_group(cls, db: Session, group_id: int) -> dict[str, Any]:
        group = cls._get_group_or_raise(db, group_id)
        db.delete(group)
        db.commit()

        return {
            "message": "Sync group deleted",
            "group_id": group_id,
        }