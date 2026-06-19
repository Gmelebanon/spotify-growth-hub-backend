import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api/trends", tags=["Trends"])

CACHE_TTL_SECONDS = 30 * 60

SUPPORTED_COUNTRIES: dict[str, dict[str, str]] = {
    "us": {"name": "US", "spotify": "us", "youtube": "us"},
    "gb": {"name": "UK", "spotify": "gb", "youtube": "gb"},
    "au": {"name": "Australia", "spotify": "au", "youtube": "au"},
    "de": {"name": "Germany", "spotify": "de", "youtube": "de"},
    "fr": {"name": "France", "spotify": "fr", "youtube": "fr"},
    "br": {"name": "Brazil", "spotify": "br", "youtube": "br"},
    "es": {"name": "Spain", "spotify": "es", "youtube": "es"},
    "it": {"name": "Italy", "spotify": "it", "youtube": "it"},
}

CACHE: dict[str, dict[str, Any]] = {}


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


def split_artist_title(value: str) -> tuple[str, str]:
    text = clean_text(value)
    if " - " not in text:
        return "", text
    artist, title = text.split(" - ", 1)
    return clean_text(artist), clean_text(title)


def detect_chart_date(html: str) -> str | None:
    match = re.search(r"(\d{4}/\d{2}/\d{2})", html)
    if not match:
        return None
    return match.group(1).replace("/", "-")


def build_source(platform: str, view: str, country: str | None) -> dict[str, str]:
    platform = platform.lower().strip()
    view = view.lower().strip()
    country = (country or "us").lower().strip()

    if country == "global":
        country_info = {"name": "Global", "spotify": "global", "youtube": "global"}
    elif country in SUPPORTED_COUNTRIES:
        country_info = SUPPORTED_COUNTRIES[country]
    else:
        raise HTTPException(status_code=400, detail="Unsupported country.")

    if platform == "spotify":
        spotify_country = country_info["spotify"]
        if view == "weekly_country":
            return {
                "title": f"Spotify Weekly Chart - {country_info['name']}",
                "url": f"https://kworb.net/spotify/country/{spotify_country}_weekly.html",
            }
        if view == "daily_country":
            return {
                "title": f"Spotify Daily Chart - {country_info['name']}",
                "url": f"https://kworb.net/spotify/country/{spotify_country}_daily.html",
            }
        if view == "us_weekly":
            return {
                "title": "Spotify Weekly Chart - US",
                "url": "https://kworb.net/spotify/country/us_weekly.html",
            }
        if view == "us_daily":
            return {
                "title": "Spotify Daily Chart - US",
                "url": "https://kworb.net/spotify/country/us_daily.html",
            }

    if platform == "youtube":
        youtube_country = country_info["youtube"]
        if view == "weekly_country":
            return {
                "title": f"YouTube Weekly Chart - {country_info['name']}",
                "url": f"https://kworb.net/youtube/insights/{youtube_country}.html",
            }
        if view == "daily_country":
            return {
                "title": f"YouTube Daily Chart - {country_info['name']}",
                "url": f"https://kworb.net/youtube/insights/{youtube_country}_daily.html",
            }
        if view == "us_weekly":
            return {
                "title": "YouTube Weekly Chart - US",
                "url": "https://kworb.net/youtube/insights/us.html",
            }
        if view == "us_daily":
            return {
                "title": "YouTube Daily Chart - US",
                "url": "https://kworb.net/youtube/insights/us_daily.html",
            }

    if platform == "aggregate":
        # Aggregate is global only. Kworb's current charts page combines multiple platforms
        # and countries as a global overview.
        return {
            "title": "Aggregate Global Current Charts",
            "url": "https://kworb.net/charts/index_a.html",
        }

    raise HTTPException(status_code=400, detail="Unsupported trends source.")


def spotify_record(cells: list[str]) -> dict[str, Any] | None:
    # Pos / P+ / Artist and Title / Days|Wks / Pk / Streams / Streams+ / 7Day? / 7Day+? / Total?
    if len(cells) < 7:
        return None
    position = parse_int(cells[0])
    artist, title = split_artist_title(cells[2])
    if position is None or not title:
        return None

    return {
        "position": position,
        "position_change": clean_text(cells[1]) or "=",
        "artist": artist,
        "title": title,
        "metric_label": "Streams",
        "metric_value": parse_int(cells[5]),
        "metric_change": parse_int(cells[6]),
        "extra_1_label": "Days/Weeks",
        "extra_1_value": clean_text(cells[3]),
        "extra_2_label": "Peak",
        "extra_2_value": clean_text(cells[4]),
        "total_label": "Total",
        "total_value": parse_int(cells[-1]),
        "raw": cells,
    }


def youtube_record(cells: list[str]) -> dict[str, Any] | None:
    # Kworb YouTube insights pages vary slightly, but usually include:
    # Pos / P+ / Title / Days / Pk / Views / Views+
    if len(cells) < 5:
        return None

    position = parse_int(cells[0])
    if position is None:
        return None

    # Find the first meaningful text cell that is not a small number/movement.
    title_index = 2 if len(cells) > 2 else 1
    artist, title = split_artist_title(cells[title_index])
    if not title:
        return None

    # Last numeric cells are usually views/change.
    numeric_values = [parse_int(cell) for cell in cells]
    numeric_present = [value for value in numeric_values if value is not None]

    metric_value = numeric_present[-2] if len(numeric_present) >= 2 else (numeric_present[-1] if numeric_present else None)
    metric_change = numeric_present[-1] if len(numeric_present) >= 2 else None

    return {
        "position": position,
        "position_change": clean_text(cells[1]) if len(cells) > 1 else "=",
        "artist": artist,
        "title": title,
        "metric_label": "Views",
        "metric_value": metric_value,
        "metric_change": metric_change,
        "extra_1_label": "Days/Weeks",
        "extra_1_value": clean_text(cells[3]) if len(cells) > 3 else "",
        "extra_2_label": "Peak",
        "extra_2_value": clean_text(cells[4]) if len(cells) > 4 else "",
        "total_label": "Total",
        "total_value": None,
        "raw": cells,
    }


def aggregate_record(cells: list[str]) -> dict[str, Any] | None:
    # Current Charts page does not have one fixed chart schema.
    # Return a clean row from the available cells so the UI can still display it.
    if len(cells) < 2:
        return None

    position = parse_int(cells[0])
    if position is None:
        return None

    title_cell = ""
    for cell in cells[1:]:
        if clean_text(cell) and parse_int(cell) is None and clean_text(cell) not in {"=", "NEW"}:
            title_cell = clean_text(cell)
            break

    if not title_cell:
        return None

    artist, title = split_artist_title(title_cell)

    return {
        "position": position,
        "position_change": clean_text(cells[1]) if len(cells) > 1 else "=",
        "artist": artist,
        "title": title,
        "metric_label": "Score",
        "metric_value": parse_int(cells[-1]),
        "metric_change": None,
        "extra_1_label": "Platform",
        "extra_1_value": "All",
        "extra_2_label": "Scope",
        "extra_2_value": "Global",
        "total_label": "Raw",
        "total_value": None,
        "raw": cells,
    }


def parse_html(html: str, platform: str, limit: int) -> list[dict[str, Any]]:
    parser = KworbTableParser()
    parser.feed(html)

    records: list[dict[str, Any]] = []
    parser_fn = spotify_record
    if platform == "youtube":
        parser_fn = youtube_record
    elif platform == "aggregate":
        parser_fn = aggregate_record

    for row in parser.rows:
        record = parser_fn(row)
        if record:
            records.append(record)

    return records[:limit]


def fetch_chart(platform: str, view: str, country: str | None, limit: int) -> dict[str, Any]:
    source = build_source(platform, view, country)

    response = requests.get(
        source["url"],
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotifyGrowthHub/1.0; +https://nerd-engine.vercel.app)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    response.raise_for_status()

    rows = parse_html(response.text, platform=platform, limit=limit)
    if not rows:
        raise ValueError("Could not find chart rows in Kworb response.")

    return {
        "platform": platform,
        "view": view,
        "country": country,
        "title": source["title"],
        "source_url": source["url"],
        "chart_date": detect_chart_date(response.text),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "rows": rows,
    }


@router.get("/chart")
def get_chart(
    platform: str = Query(default="spotify", pattern="^(spotify|youtube|aggregate)$"),
    view: str = Query(default="weekly_country"),
    country: str = Query(default="us"),
    limit: int = Query(default=200, ge=1, le=500),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    cache_key = f"{platform}:{view}:{country}:{limit}"
    cached = CACHE.get(cache_key)
    now = time.time()

    if not refresh and cached and now - float(cached["cached_at"]) < CACHE_TTL_SECONDS:
        payload = dict(cached["payload"])
        payload["cached"] = True
        return payload

    try:
        payload = fetch_chart(platform, view, country, limit)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Kworb chart: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    CACHE[cache_key] = {"cached_at": now, "payload": payload}
    payload["cached"] = False
    return payload


@router.get("/spotify-global-weekly")
def legacy_spotify_global_weekly(
    limit: int = Query(default=200, ge=1, le=500),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    return get_chart(
        platform="spotify",
        view="weekly_country",
        country="global",
        limit=limit,
        refresh=refresh,
    )
