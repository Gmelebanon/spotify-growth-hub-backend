from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.playlist import Playlist
from app.models.follower_history import FollowerHistory
from app.schemas.playlist import PlaylistOut, PlaylistGrowthOut
from app.schemas.follower_history import FollowerHistoryOut


def _compute_growth(history_records: list[FollowerHistory]) -> int:
    """Return growth from earliest to latest history row. 0 if fewer than 2 rows."""
    if len(history_records) < 2:
        return 0
    return history_records[-1].followers - history_records[0].followers


def _history_for_playlist(db: Session, playlist_id: int) -> list[FollowerHistory]:
    """Return all history rows for a playlist ordered oldest to newest."""
    return (
        db.query(FollowerHistory)
        .filter(FollowerHistory.playlist_id == playlist_id)
        .order_by(FollowerHistory.date.asc())
        .all()
    )


def _build_playlist_out(playlist: Playlist, growth: int) -> PlaylistOut:
    return PlaylistOut(
        id=playlist.id,
        name=playlist.name,
        followers=playlist.followers,
        created_at=playlist.created_at,
        growth=growth,
    )


def get_all_playlists(
    db: Session,
    search: str | None = None,
    growth_direction: str | None = None,
    sort_by: str | None = "created_at_desc",
) -> list[PlaylistOut]:
    playlists = db.query(Playlist).all()

    # Attach growth to each playlist
    results: list[tuple[Playlist, int]] = []
    for p in playlists:
        history = _history_for_playlist(db, p.id)
        growth = _compute_growth(history)
        results.append((p, growth))

    # Filter: search by name (case-insensitive)
    if search:
        term = search.lower()
        results = [(p, g) for p, g in results if term in p.name.lower()]

    # Filter: growth direction
    if growth_direction == "growing":
        results = [(p, g) for p, g in results if g > 0]
    elif growth_direction == "declining":
        results = [(p, g) for p, g in results if g < 0]
    elif growth_direction == "flat":
        results = [(p, g) for p, g in results if g == 0]

    # Sort
    sort_map = {
        "created_at_desc": lambda x: x[0].created_at,
        "created_at_asc":  lambda x: x[0].created_at,
        "followers_desc":  lambda x: x[0].followers,
        "followers_asc":   lambda x: x[0].followers,
        "growth_desc":     lambda x: x[1],
        "growth_asc":      lambda x: x[1],
    }
    reverse_map = {
        "created_at_desc": True,
        "created_at_asc":  False,
        "followers_desc":  True,
        "followers_asc":   False,
        "growth_desc":     True,
        "growth_asc":      False,
    }
    key_fn = sort_map.get(sort_by, sort_map["created_at_desc"])
    reverse = reverse_map.get(sort_by, True)
    results.sort(key=key_fn, reverse=reverse)

    return [_build_playlist_out(p, g) for p, g in results]


def get_playlist_growth(db: Session, playlist_id: int) -> PlaylistGrowthOut | None:
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        return None

    history_records = _history_for_playlist(db, playlist_id)
    growth = _compute_growth(history_records)
    history_out = [FollowerHistoryOut.model_validate(r) for r in history_records]

    return PlaylistGrowthOut(
        playlist_id=playlist.id,
        current_followers=playlist.followers,
        growth=growth,
        history=history_out,
    )