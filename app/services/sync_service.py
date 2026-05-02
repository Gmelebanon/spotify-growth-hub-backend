import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.sync_group import SyncGroup
from app.schemas.sync import SyncGroupCreate, SyncGroupOut, SyncExecuteResponse

# Pause between each child playlist operation to simulate sequential processing
CHILD_OPERATION_DELAY = 0.3

# Simulated master playlist track count used when no real track data is available.
# In a future phase this will be replaced with a real lookup against the Playlist model.
SIMULATED_TRACK_COUNT = 24


def _to_schema(group: SyncGroup) -> SyncGroupOut:
    return SyncGroupOut(
        id=group.id,
        name=group.name,
        master_playlist_id=group.master_playlist_id,
        child_playlist_ids=group.child_playlist_ids,
        created_at=group.created_at,
    )


def create_sync_group(db: Session, payload: SyncGroupCreate) -> SyncGroupOut:
    group = SyncGroup(
        name=payload.name,
        master_playlist_id=payload.master_playlist_id,
    )
    # Use the property setter so serialisation is handled consistently
    group.child_playlist_ids = payload.child_playlist_ids

    db.add(group)
    db.commit()
    db.refresh(group)
    return _to_schema(group)


def get_all_sync_groups(db: Session) -> list[SyncGroupOut]:
    groups = db.query(SyncGroup).order_by(SyncGroup.created_at.desc()).all()
    return [_to_schema(g) for g in groups]


def execute_sync(db: Session, group_id: int) -> SyncExecuteResponse | None:
    """
    Simulate a sync operation for the given group.

    Steps:
      1. Fetch the SyncGroup record from the database.
      2. Simulate reading tracks from the master playlist.
      3. Iterate child playlists sequentially, pausing 0.3s between each.
      4. Build an execution log describing every step taken.
      5. Return the result — no database writes, no Spotify calls.
    """
    group = db.query(SyncGroup).filter(SyncGroup.id == group_id).first()
    if not group:
        return None

    child_ids = group.child_playlist_ids
    log: list[str] = []

    started_at = datetime.now(timezone.utc).isoformat()
    log.append(f"[{started_at}] Sync started for group '{group.name}' (id={group.id})")

    # Step 1 — simulate reading the master playlist
    log.append(
        f"[MASTER] Reading tracks from master playlist id={group.master_playlist_id}"
    )
    tracks_count = SIMULATED_TRACK_COUNT
    log.append(
        f"[MASTER] Found {tracks_count} tracks — ready to distribute to "
        f"{len(child_ids)} child playlist(s)"
    )

    if not child_ids:
        log.append("[WARN] No child playlists defined in this sync group — nothing to sync")
        return SyncExecuteResponse(
            group_id=group.id,
            master_playlist_id=group.master_playlist_id,
            number_of_child_playlists=0,
            tracks_copied=0,
            execution_log=log,
        )

    # Step 2 — iterate each child playlist sequentially
    for index, child_id in enumerate(child_ids, start=1):
        step_ts = datetime.now(timezone.utc).isoformat()
        log.append(
            f"[{step_ts}] [{index}/{len(child_ids)}] "
            f"Simulating copy of {tracks_count} tracks → child playlist id={child_id}"
        )

        # Controlled pause between child operations — no parallelism
        time.sleep(CHILD_OPERATION_DELAY)

        done_ts = datetime.now(timezone.utc).isoformat()
        log.append(
            f"[{done_ts}] [{index}/{len(child_ids)}] "
            f"Done — child playlist id={child_id} updated (simulated)"
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    log.append(
        f"[{finished_at}] Sync complete — "
        f"{tracks_count} tracks distributed to {len(child_ids)} child playlist(s)"
    )

    return SyncExecuteResponse(
        group_id=group.id,
        master_playlist_id=group.master_playlist_id,
        number_of_child_playlists=len(child_ids),
        tracks_copied=tracks_count,
        execution_log=log,
    )