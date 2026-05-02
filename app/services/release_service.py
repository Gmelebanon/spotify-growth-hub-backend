from sqlalchemy.orm import Session

from app.models.release import Release
from app.schemas.release import ReleaseCreate, ReleaseOut


def _to_schema(release: Release) -> ReleaseOut:
    return ReleaseOut(
        id=release.id,
        upc=release.upc,
        artist=release.artist,
        status=release.status,
        created_at=release.created_at,
    )


def create_release(db: Session, payload: ReleaseCreate) -> ReleaseOut:
    release = Release(
        upc=payload.upc,
        artist=payload.artist,
        status=payload.status,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return _to_schema(release)


def get_all_releases(db: Session) -> list[ReleaseOut]:
    releases = db.query(Release).order_by(Release.created_at.desc()).all()
    return [_to_schema(r) for r in releases]