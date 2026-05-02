import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.sync_group_service import SyncGroupService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sync-groups"])


class CreateSyncGroupRequest(BaseModel):
    name: str = Field(..., min_length=1)
    master_playlist_id: Optional[int] = None
    child_playlist_ids: list[int] = Field(default_factory=list)


class AddChildPlaylistRequest(BaseModel):
    playlist_id: int = Field(..., gt=0)


class CacheSyncGroupRequest(BaseModel):
    enabled: bool = True


class AddTrackRequest(BaseModel):
    track_name: str = Field(..., min_length=1)
    artist_name: Optional[str] = None


@router.get("/accounts/{account_id}/sync-groups")
def get_sync_groups(account_id: int, db: Session = Depends(get_db)):
    try:
        return {"items": SyncGroupService.list_groups(db, account_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in get_sync_groups")
        raise HTTPException(status_code=500, detail=f"sync_groups_get_failed: {str(exc)}") from exc


@router.post("/accounts/{account_id}/sync-groups")
def create_sync_group(account_id: int, payload: CreateSyncGroupRequest, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.create_group(
            db=db,
            account_id=account_id,
            name=payload.name,
            master_playlist_id=payload.master_playlist_id,
            child_playlist_ids=payload.child_playlist_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Database integrity error while creating sync group") from exc
    except Exception as exc:
        logger.exception("Unhandled error in create_sync_group")
        raise HTTPException(status_code=500, detail=f"sync_groups_create_failed: {str(exc)}") from exc


@router.post("/sync-groups/{group_id}/children")
def add_sync_group_child(group_id: int, payload: AddChildPlaylistRequest, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.add_child_playlist(
            db=db,
            group_id=group_id,
            playlist_id=payload.playlist_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Database integrity error while adding child playlist") from exc
    except Exception as exc:
        logger.exception("Unhandled error in add_sync_group_child")
        raise HTTPException(status_code=500, detail=f"sync_groups_add_child_failed: {str(exc)}") from exc


@router.delete("/sync-groups/{group_id}/children/{child_id}")
def delete_sync_group_child(group_id: int, child_id: int, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.remove_child_playlist(
            db=db,
            group_id=group_id,
            child_id=child_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in delete_sync_group_child")
        raise HTTPException(status_code=500, detail=f"sync_groups_delete_child_failed: {str(exc)}") from exc


@router.post("/sync-groups/{group_id}/sync")
def sync_group(group_id: int, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.sync_group(db, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in sync_group")
        raise HTTPException(status_code=500, detail=f"sync_groups_sync_failed: {str(exc)}") from exc


@router.post("/sync-groups/{group_id}/cache")
def cache_sync_group(group_id: int, payload: CacheSyncGroupRequest, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.set_cached_for_quick_scan(
            db=db,
            group_id=group_id,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in cache_sync_group")
        raise HTTPException(status_code=500, detail=f"sync_groups_cache_failed: {str(exc)}") from exc


@router.post("/sync-groups/{group_id}/add-track")
def add_track_to_sync_group(group_id: int, payload: AddTrackRequest, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.add_one_track(
            db=db,
            group_id=group_id,
            track_name=payload.track_name,
            artist_name=payload.artist_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in add_track_to_sync_group")
        raise HTTPException(status_code=500, detail=f"sync_groups_add_track_failed: {str(exc)}") from exc


@router.delete("/sync-groups/{group_id}")
def delete_sync_group(group_id: int, db: Session = Depends(get_db)):
    try:
        return SyncGroupService.delete_group(db, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in delete_sync_group")
        raise HTTPException(status_code=500, detail=f"sync_groups_delete_failed: {str(exc)}") from exc