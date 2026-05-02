import time
from datetime import datetime

from pytrends.request import TrendReq
from pytrends.exceptions import ResponseError

from app.schemas.trends import TrendItem, TrendsResponse

# Default seeds used when no seed_keyword is provided
DEFAULT_SEEDS = [
    "workout playlist",
    "running music",
    "gym songs",
    "focus music",
    "chill playlist",
    "afrobeats",
    "house music",
    "sad songs",
]

# Minimum pause between pytrends requests to avoid rate limiting
REQUEST_PAUSE_SECONDS = 1.2

CURRENT_YEAR = datetime.now().year


def _build_pytrends_client() -> TrendReq:
    """Instantiate a pytrends client with conservative backoff settings."""
    return TrendReq(
        hl="en-US",
        tz=0,
        timeout=(10, 25),
        retries=1,
        backoff_factor=0.5,
    )


def _score_from_value(value) -> int:
    """
    Convert a pytrends rising/top query value to a 0–100 trend score.

    pytrends returns rising queries with a numeric value:
      - Positive int (e.g. 250)  = percentage increase in search interest
      - The string "Breakout"    = >5000% increase — treat as maximum signal (100)
      - 0 or missing             = no signal (0)

    Scoring rule:
      - "Breakout" → 100
      - value >= 500 → 90
      - value >= 200 → 75
      - value >= 100 → 60
      - value >= 50  → 45
      - value >= 10  → 30
      - value >  0   → 15
      - else         → 0
    """
    if value == "Breakout":
        return 100
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0

    if v >= 500:
        return 90
    if v >= 200:
        return 75
    if v >= 100:
        return 60
    if v >= 50:
        return 45
    if v >= 10:
        return 30
    if v > 0:
        return 15
    return 0


def _growth_direction(score: int) -> str:
    """
    Derive growth direction from trend score.

    Rule:
      score >= 45  → "up"   (meaningful positive interest)
      score >= 15  → "flat" (some signal but not strongly rising)
      score == 0   → "down" (no detectable rising signal)
    """
    if score >= 45:
        return "up"
    if score >= 15:
        return "flat"
    return "down"


def _playlist_idea(keyword: str) -> str:
    """
    Generate a playlist title from the keyword and the current year.
    Capitalises the keyword and appends year + Playlist suffix.
    """
    base = keyword.title()
    return f"{base} {CURRENT_YEAR} Playlist"


def _fetch_rising_for_keyword(
    pytrends: TrendReq, keyword: str
) -> list[tuple[str, object]]:
    """
    Fetch related rising queries for a single keyword from Google Trends.
    Returns a list of (query_string, value) tuples.
    Returns [] safely on any error or empty response.
    """
    try:
        pytrends.build_payload(
            kw_list=[keyword],
            cat=0,
            timeframe="now 7-d",
            geo="",
            gprop="",
        )
        time.sleep(REQUEST_PAUSE_SECONDS)

        related = pytrends.related_queries()

        # related is a dict keyed by keyword; each value has "top" and "rising" DataFrames
        kw_data = related.get(keyword, {})
        rising_df = kw_data.get("rising") if kw_data else None

        if rising_df is None or rising_df.empty:
            return []

        # Each row has columns: "query" and "value"
        return list(zip(rising_df["query"].tolist(), rising_df["value"].tolist()))

    except ResponseError:
        # Google Trends rate-limited or blocked this request
        return []
    except Exception:
        # Network error, malformed response, or any other pytrends failure
        return []


def get_trends(
    seed_keyword: str | None = None,
    limit: int = 10,
) -> TrendsResponse:
    """
    Main service function.

    If seed_keyword is provided, fetch rising queries for that single keyword.
    Otherwise, cycle through DEFAULT_SEEDS and collect candidates from each,
    deduplicating as we go, until we have enough to fill `limit`.
    """
    pytrends = _build_pytrends_client()

    seeds = [seed_keyword] if seed_keyword else DEFAULT_SEEDS
    seed_label = seed_keyword if seed_keyword else ", ".join(DEFAULT_SEEDS)

    # Collect (query, value) pairs across seeds, deduplicating by lowercased query
    seen_queries: set[str] = set()
    candidates: list[tuple[str, object]] = []

    for seed in seeds:
        if len(candidates) >= limit * 3:
            # We have plenty of raw material — avoid unnecessary API calls
            break

        raw_pairs = _fetch_rising_for_keyword(pytrends, seed)

        for query, value in raw_pairs:
            normalized = query.lower().strip()
            if normalized and normalized not in seen_queries:
                seen_queries.add(normalized)
                candidates.append((query.strip(), value))

        if len(seeds) > 1:
            # Brief pause between seed requests when cycling defaults
            time.sleep(REQUEST_PAUSE_SECONDS)

    # Build scored TrendItem objects
    scored: list[TrendItem] = []
    for query, value in candidates:
        score = _score_from_value(value)
        scored.append(
            TrendItem(
                keyword=query,
                trend_score=score,
                growth_direction=_growth_direction(score),
                playlist_idea=_playlist_idea(query),
            )
        )

    # Sort by trend_score descending so highest-signal items appear first
    scored.sort(key=lambda x: x.trend_score, reverse=True)

    # Apply limit
    results = scored[:limit]

    return TrendsResponse(
        seed_keyword_used=seed_label,
        total_results=len(results),
        results=results,
    )