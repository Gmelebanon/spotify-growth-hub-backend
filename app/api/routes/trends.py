import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api/trends", tags=["Trends"])

KWORB_GLOBAL_WEEKLY_URL = "https://kworb.net/spotify/country/global_weekly.html"
CACHE_TTL_SECONDS = 30 * 60

_CACHE: dict[str, Any] = {
    "fetched_at": 0.0,
    "payload": None,
}


class KworbTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            cell = clean_text(" ".join(self.current_cell))
            self.current_row.append(cell)
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
            self.in_row = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None

    cleaned = clean_text(value).replace(",", "")
    if cleaned in {"", "-", "="}:
        return None

    cleaned = re.sub(r"[^\d-]", "", cleaned)
    if cleaned in {"", "-"}:
        return None

    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_position_change(value: str | None) -> str:
    if not value:
        return "="
    cleaned = clean_text(value)
    return cleaned or "="


def split_artist_title(value: str) -> tuple[str, str]:
    text = clean_text(value)
    if " - " not in text:
        return "", text

    artist, title = text.split(" - ", 1)
    return clean_text(artist), clean_text(title)


def extract_chart_date(html: str) -> str | None:
    match = re.search(r"Spotify Weekly Chart\s*-\s*Global\s*-\s*(\d{4}/\d{2}/\d{2})", html)
    if not match:
        return None
    return match.group(1).replace("/", "-")


def row_to_record(cells: list[str]) -> dict[str, Any] | None:
    # Expected Kworb columns:
    # Pos / P+ / Artist and Title / Wks / Pk(x?) / Streams / Streams+ / Total
    if len(cells) < 8:
        return None

    position = parse_int(cells[0])
    artist, title = split_artist_title(cells[2])

    if position is None or not title:
        return None

    return {
        "position": position,
        "position_change": parse_position_change(cells[1]),
        "artist": artist,
        "title": title,
        "weeks": parse_int(cells[3]),
        "peak": clean_text(cells[4]),
        "streams": parse_int(cells[5]),
        "streams_change": parse_int(cells[6]),
        "total_streams": parse_int(cells[7]),
    }


def parse_kworb_html(html: str, limit: int) -> dict[str, Any]:
    parser = KworbTableParser()
    parser.feed(html)

    records: list[dict[str, Any]] = []

    for row in parser.rows:
        record = row_to_record(row)
        if record:
            records.append(record)

    if not records:
        raise ValueError("Could not find chart rows in Kworb response.")

    chart_date = extract_chart_date(html)

    return {
        "source": "Kworb Spotify Weekly Chart - Global",
        "source_url": KWORB_GLOBAL_WEEKLY_URL,
        "chart_date": chart_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": min(len(records), limit),
        "rows": records[:limit],
    }


def fetch_kworb_trends(limit: int) -> dict[str, Any]:
    response = requests.get(
        KWORB_GLOBAL_WEEKLY_URL,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotifyGrowthHub/1.0; +https://nerd-engine.vercel.app)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    response.raise_for_status()
    return parse_kworb_html(response.text, limit=limit)


@router.get("/spotify-global-weekly")
def get_spotify_global_weekly(
    limit: int = Query(default=200, ge=1, le=500),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    now = time.time()
    cached_payload = _CACHE.get("payload")
    fetched_at = float(_CACHE.get("fetched_at") or 0)

    if not refresh and cached_payload and now - fetched_at < CACHE_TTL_SECONDS:
        payload = dict(cached_payload)
        payload["cached"] = True
        payload["rows"] = payload["rows"][:limit]
        payload["count"] = len(payload["rows"])
        return payload

    try:
        payload = fetch_kworb_trends(limit=max(limit, 500))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Kworb trends: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _CACHE["fetched_at"] = now
    _CACHE["payload"] = payload

    returned = dict(payload)
    returned["cached"] = False
    returned["rows"] = returned["rows"][:limit]
    returned["count"] = len(returned["rows"])
    return returned
