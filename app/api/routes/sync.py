from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.sync import (
    SyncGroupCreate,
    SyncGroupOut,
    SyncExecuteRequest,
    SyncExecuteResponse,
)
from app.services.sync_service import create_sync_group, get_all_sync_groups, execute_sync

router = APIRouter(prefix="/sync", tags=["Sync"])


@router.post("/groups", response_model=SyncGroupOut, status_code=201)
def create_group(payload: SyncGroupCreate, db: Session = Depends(get_db)):
    """Create a new sync group with a master playlist and one or more child playlists."""
    return create_sync_group(db, payload)


@router.get("/groups", response_model=list[SyncGroupOut])
def list_groups(db: Session = Depends(get_db)):
    """Return all sync groups ordered by created_at descending."""
    return get_all_sync_groups(db)


@router.post("", response_model=SyncExecuteResponse)
def run_sync(payload: SyncExecuteRequest, db: Session = Depends(get_db)):
    """
    Execute a simulated sync for the given group_id.
    Sequentially processes each child playlist with a 0.3s delay between operations.
    No Spotify API calls. No database writes during execution.
    """
    result = execute_sync(db, payload.group_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sync group with id={payload.group_id} not found",
        )
    return result