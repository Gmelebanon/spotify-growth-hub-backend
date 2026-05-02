from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.release import ReleaseCreate, ReleaseOut
from app.services.release_service import create_release, get_all_releases

router = APIRouter(prefix="/releases", tags=["Releases"])


@router.post("", response_model=ReleaseOut, status_code=201)
def create(payload: ReleaseCreate, db: Session = Depends(get_db)):
    return create_release(db, payload)


@router.get("", response_model=list[ReleaseOut])
def list_all(db: Session = Depends(get_db)):
    return get_all_releases(db)