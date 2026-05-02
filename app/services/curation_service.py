from collections import deque

from app.schemas.curation import TrackIn, TrackOut, CurateRequest, CurateResponse

# How many positions back we check to enforce artist spacing
ARTIST_SPACING = 3


def _recent_artists(ordered: list[TrackOut], window: int = ARTIST_SPACING) -> set[str]:
    """Return the set of artist names in the last `window` placed tracks."""
    return {t.artist_name for t in ordered[-window:]}


def _try_place(
    pool: deque[TrackIn],
    ordered: list[TrackOut],
    seen_ids: set[str],
    skipped_duplicates: list[int],
    skipped_artist_spacing: list[int],
    slots: int,
) -> int:
    """
    Attempt to place up to `slots` tracks from `pool` into `ordered`.
    Skips duplicates and artist-spacing violations.
    Returns the number of tracks successfully placed.
    """
    placed = 0
    attempts = 0
    # We scan the entire remaining pool at most once per slot attempt
    max_attempts = len(pool)

    while placed < slots and pool and attempts < max_attempts:
        candidate = pool.popleft()
        attempts += 1

        # Duplicate check
        if candidate.track_id in seen_ids:
            skipped_duplicates[0] += 1
            continue

        # Artist spacing check
        if candidate.artist_name in _recent_artists(ordered):
            # Re-queue at the back — another slot or pool pass may place it
            pool.append(candidate)
            skipped_artist_spacing[0] += 1
            continue

        # Track passes all checks — place it
        ordered.append(
            TrackOut(
                track_id=candidate.track_id,
                track_name=candidate.track_name,
                artist_name=candidate.artist_name,
            )
        )
        seen_ids.add(candidate.track_id)
        placed += 1
        attempts = 0  # reset attempt counter after a successful placement

    return placed


def run_curation(request: CurateRequest) -> CurateResponse:
    source_pool = deque(request.source_playlist_tracks)
    my_pool = deque(request.my_tracks)

    source_slots = request.ratio[0]  # e.g. 3 — number of source tracks per cycle
    my_slots = request.ratio[1]      # e.g. 1 — number of my tracks per cycle

    ordered: list[TrackOut] = []
    seen_ids: set[str] = set()

    # Use mutable containers so helper functions can mutate them
    skipped_duplicates = [0]
    skipped_artist_spacing = [0]

    source_used = 0
    my_used = 0

    while True:
        before = len(ordered)

        # Place source_slots tracks from the source pool
        placed_source = _try_place(
            pool=source_pool,
            ordered=ordered,
            seen_ids=seen_ids,
            skipped_duplicates=skipped_duplicates,
            skipped_artist_spacing=skipped_artist_spacing,
            slots=source_slots,
        )
        source_used += placed_source

        # Place my_slots tracks from my pool
        placed_my = _try_place(
            pool=my_pool,
            ordered=ordered,
            seen_ids=seen_ids,
            skipped_duplicates=skipped_duplicates,
            skipped_artist_spacing=skipped_artist_spacing,
            slots=my_slots,
        )
        my_used += placed_my

        after = len(ordered)

        # If no tracks were placed in this full cycle, both pools are exhausted
        # or every remaining candidate violates spacing/duplicate rules — stop.
        if after == before:
            break

    # Exhaustion fallback: if one pool ran dry mid-cycle, drain the other
    # This handles cases where ratio cycling ends but one pool still has valid tracks.
    while source_pool:
        placed = _try_place(
            pool=source_pool,
            ordered=ordered,
            seen_ids=seen_ids,
            skipped_duplicates=skipped_duplicates,
            skipped_artist_spacing=skipped_artist_spacing,
            slots=len(source_pool),
        )
        source_used += placed
        if placed == 0:
            break  # Nothing placeable remains

    while my_pool:
        placed = _try_place(
            pool=my_pool,
            ordered=ordered,
            seen_ids=seen_ids,
            skipped_duplicates=skipped_duplicates,
            skipped_artist_spacing=skipped_artist_spacing,
            slots=len(my_pool),
        )
        my_used += placed
        if placed == 0:
            break  # Nothing placeable remains

    return CurateResponse(
        ordered_tracks=ordered,
        total_tracks=len(ordered),
        source_tracks_used=source_used,
        my_tracks_used=my_used,
        skipped_duplicates=skipped_duplicates[0],
        skipped_artist_spacing=skipped_artist_spacing[0],
    )