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
    "global": {"name": "Global", "spotify": "global", "youtube": "global"},
    "us": {"name": "US", "spotify": "us", "youtube": "us"},
    "gb": {"name": "UK", "spotify": "gb", "youtube": "uk"},
    "au": {"name": "Australia", "spotify": "au", "youtube": "au"},
    "de": {"name": "Germany", "spotify": "de", "youtube": "de"},
    "fr": {"name": "France", "spotify": "fr", "youtube": "fr"},
    "br": {"name": "Brazil", "spotify": "br", "youtube": "br"},
    "es": {"name": "Spain", "spotify": "es", "youtube": "es"},
    "it": {"name": "Italy", "spotify": "it", "youtube": "it"},
}

TIKTOK_COUNTRIES: dict[str, dict[str, str]] = {
    "worldwide": {"name": "Worldwide", "slug": "worldwide"},
    "global": {"name": "Worldwide", "slug": "worldwide"},
    "us": {"name": "US", "slug": "united-states"},
    "gb": {"name": "UK", "slug": "united-kingdom"},
    "au": {"name": "Australia", "slug": "australia"},
    "de": {"name": "Germany", "slug": "germany"},
    "fr": {"name": "France", "slug": "france"},
    "br": {"name": "Brazil", "slug": "brazil"},
    "es": {"name": "Spain", "slug": "spain"},
    "it": {"name": "Italy", "slug": "italy"},
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
            self.current_row.append(clean_text(" ".join(self.current_cell)))
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



def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; NerdEngineTrends/1.0; +https://nerd-engine.vercel.app)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def build_source(platform: str, view: str, country: str | None) -> dict[str, str]:
    platform = platform.lower().strip()
    view = view.lower().strip()
    country = (country or "us").lower().strip()

    if platform == "tiktok":
        if country not in TIKTOK_COUNTRIES:
            raise HTTPException(status_code=400, detail="Unsupported TikTok country.")

        tiktok_country = TIKTOK_COUNTRIES[country]
        period = "7-days" if view in {"weekly_country", "us_weekly"} else "1-day"

        return {
            "title": f"TikTok Creations - {tiktok_country['name']} - {'Weekly' if period == '7-days' else 'Daily'}",
            "url": f"https://chartex.com/tiktok/songs/{period}/{tiktok_country['slug']}?min_count=10000",
        }

    if country not in SUPPORTED_COUNTRIES:
        raise HTTPException(status_code=400, detail="Unsupported country.")

    country_info = SUPPORTED_COUNTRIES[country]

    if platform == "spotify":
        code = country_info["spotify"]

        if view in {"weekly_country", "global_weekly"}:
            return {
                "title": f"Spotify Weekly Chart - {country_info['name']}",
                "url": f"https://kworb.net/spotify/country/{code}_weekly.html",
            }

        if view in {"daily_country", "global_daily"}:
            return {
                "title": f"Spotify Daily Chart - {country_info['name']}",
                "url": f"https://kworb.net/spotify/country/{code}_daily.html",
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
        code = country_info["youtube"]

        if code == "global":
            raise HTTPException(status_code=400, detail="YouTube global country chart is not supported.")

        if view == "weekly_country":
            return {
                "title": f"YouTube Weekly Chart - {country_info['name']}",
                "url": f"https://kworb.net/youtube/insights/{code}.html",
            }

        if view == "daily_country":
            return {
                "title": f"YouTube Daily Chart - {country_info['name']}",
                "url": f"https://kworb.net/youtube/insights/{code}_daily.html",
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
        return {
            "title": "Aggregate Current Charts",
            "url": "https://kworb.net/charts/",
        }

    raise HTTPException(status_code=400, detail="Unsupported trends source.")


def spotify_record(cells: list[str]) -> dict[str, Any] | None:
    # Kworb Spotify columns normally:
    # Pos / P+ / Artist and Title / Days|Wks / Pk / Streams / Streams+ / Total...
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
    if len(cells) < 5:
        return None

    position = parse_int(cells[0])
    if position is None:
        return None

    title_cell = cells[2] if len(cells) > 2 else cells[1]
    artist, title = split_artist_title(title_cell)
    if not title:
        return None

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
    # Kworb /charts/ rows:
    # Country / iTunes / Spotify / Apple Music / YouTube / Shazam / Deezer
    if len(cells) < 7:
        return None

    country = clean_text(cells[0])
    if not country:
        return None

    ignored = {
        "country",
        "main countries:",
        "other countries:",
        "itunes",
        "spotify",
        "apple music",
        "youtube",
        "shazam",
        "deezer",
    }

    if country.lower() in ignored:
        return None

    # Skip non-country header rows.
    if country.lower().startswith("current charts"):
        return None

    return {
        "position": 0,
        "position_change": "",
        "artist": "",
        "title": country,
        "metric_label": "Aggregate",
        "metric_value": None,
        "metric_change": None,
        "extra_1_label": "iTunes",
        "extra_1_value": clean_text(cells[1]) if len(cells) > 1 else "",
        "extra_2_label": "Spotify",
        "extra_2_value": clean_text(cells[2]) if len(cells) > 2 else "",
        "total_label": "YouTube",
        "total_value": None,
        "country": country,
        "itunes": clean_text(cells[1]) if len(cells) > 1 else "",
        "spotify": clean_text(cells[2]) if len(cells) > 2 else "",
        "apple_music": clean_text(cells[3]) if len(cells) > 3 else "",
        "youtube": clean_text(cells[4]) if len(cells) > 4 else "",
        "shazam": clean_text(cells[5]) if len(cells) > 5 else "",
        "deezer": clean_text(cells[6]) if len(cells) > 6 else "",
        "raw": cells,
    }


TIKTOK_NAV_TITLES = {
    "home",
    "tiktok songs",
    "all time",
    "sounds",
    "songs",
    "artists",
    "creators",
    "others",
    "pricing",
    "login",
    "filters",
    "creates",
    "streams",
    "views",
    "shazams",
    "evolution:creates, streams",
    "rank song title label / distributor metric descr.",
    "rank song title record label metric descr.",
}


def is_bad_tiktok_title(value: str) -> bool:
    title = clean_text(value).lower()

    if not title:
        return True

    if title in TIKTOK_NAV_TITLES:
        return True

    if re.fullmatch(r"\d{1,2}\s+[a-z]{3}\s+\d{4}\s*-\s*\d{1,2}\s+[a-z]{3}\s+\d{4}", title):
        return True

    # Country/filter names appear as fake rows in Chartex's public HTML when the
    # real chart data is not rendered server-side. Do not show them as songs.
    country_names = {item["name"].lower() for item in TIKTOK_COUNTRIES.values()}
    country_names.update({"united states", "united kingdom", "worldwide"})
    if title in country_names:
        return True

    return False


def tiktok_record(cells: list[str]) -> dict[str, Any] | None:
    if len(cells) < 3:
        return None

    position = parse_int(cells[0])
    if position is None or position < 1 or position > 500:
        return None

    title = clean_text(cells[1]) if len(cells) > 1 else ""
    possible_artist = clean_text(cells[2]) if len(cells) > 2 else ""

    if is_bad_tiktok_title(title):
        return None

    if title.lower().startswith("top tiktok songs"):
        return None

    artist = ""

    if " - " in title:
        artist, title = split_artist_title(title)
    elif (
        possible_artist
        and parse_int(possible_artist) is None
        and not is_bad_tiktok_title(possible_artist)
        and possible_artist.lower() not in {"label / distributor", "record label", "metric descr."}
    ):
        artist = possible_artist

    if is_bad_tiktok_title(title):
        return None

    return {
        "position": position,
        "position_change": "=",
        "artist": artist,
        "title": title,
        "metric_label": "Creates",
        "metric_value": None,
        "metric_change": None,
        "extra_1_label": "Source",
        "extra_1_value": "Chartex",
        "extra_2_label": "Platform",
        "extra_2_value": "TikTok",
        "total_label": "Total",
        "total_value": None,
        "raw": cells,
    }


def walk_json_for_tiktok_rows(value: Any, rows: list[dict[str, Any]]) -> None:
    if len(rows) >= 500:
        return

    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}

        title_value = (
            lowered.get("title")
            or lowered.get("songtitle")
            or lowered.get("song_title")
            or lowered.get("name")
            or lowered.get("tracktitle")
        )
        artist_value = (
            lowered.get("artist")
            or lowered.get("artistname")
            or lowered.get("artist_name")
            or lowered.get("author")
            or lowered.get("creator")
        )
        rank_value = lowered.get("rank") or lowered.get("position") or lowered.get("chartposition")

        if title_value and rank_value is not None and not is_bad_tiktok_title(str(title_value)):
            position = parse_int(str(rank_value))
            if position is not None:
                rows.append({
                    "position": position,
                    "position_change": "=",
                    "artist": clean_text(str(artist_value or "")),
                    "title": clean_text(str(title_value)),
                    "metric_label": "Creates",
                    "metric_value": None,
                    "metric_change": None,
                    "extra_1_label": "Source",
                    "extra_1_value": "Chartex",
                    "extra_2_label": "Platform",
                    "extra_2_value": "TikTok",
                    "total_label": "Total",
                    "total_value": None,
                    "raw": [],
                })

        for child in value.values():
            walk_json_for_tiktok_rows(child, rows)

    elif isinstance(value, list):
        for child in value:
            walk_json_for_tiktok_rows(child, rows)


def parse_tiktok_html(html: str, limit: int) -> list[dict[str, Any]]:
    parser = KworbTableParser()
    parser.feed(html)

    rows: list[dict[str, Any]] = []
    for row in parser.rows:
        record = tiktok_record(row)
        if record:
            rows.append(record)

    if rows:
        return rows[:limit]

    # Fallback: attempt to find embedded JSON used by modern app pages.
    import json

    json_candidates = re.findall(r'<script[^>]*>(.*?)</script>', html, flags=re.DOTALL | re.IGNORECASE)
    json_rows: list[dict[str, Any]] = []

    for candidate in json_candidates:
        candidate = candidate.strip()
        if not candidate or ("song" not in candidate.lower() and "title" not in candidate.lower()):
            continue

        # Plain JSON script.
        if candidate.startswith("{") or candidate.startswith("["):
            try:
                data = json.loads(candidate)
                walk_json_for_tiktok_rows(data, json_rows)
            except Exception:
                pass

        # Next app/router serialized chunks may contain escaped JSON. Try broad object snippets.
        for match in re.finditer(r'(\{[^{}]{0,5000}"(?:title|songTitle|name|rank|position)"[^{}]{0,5000}\})', candidate):
            try:
                data = json.loads(match.group(1))
                walk_json_for_tiktok_rows(data, json_rows)
            except Exception:
                continue

    seen: set[tuple[int, str, str]] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in json_rows:
        key = (int(row.get("position") or 0), str(row.get("title") or ""), str(row.get("artist") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    return sorted(unique_rows, key=lambda item: int(item.get("position") or 999999))[:limit]


def parse_html(html: str, platform: str, limit: int) -> list[dict[str, Any]]:
    if platform == "tiktok":
        return parse_tiktok_html(html, limit)

    parser = KworbTableParser()
    parser.feed(html)

    parser_fn = spotify_record
    if platform == "youtube":
        parser_fn = youtube_record
    elif platform == "aggregate":
        parser_fn = aggregate_record

    rows: list[dict[str, Any]] = []

    for row in parser.rows:
        record = parser_fn(row)
        if record:
            rows.append(record)

    return rows[:limit]



def fetch_chart(platform: str, view: str, country: str | None, limit: int, refresh: bool) -> dict[str, Any]:
    source = build_source(platform, view, country)
    source_url = source["url"]

    # Important: cache must include platform AND final source URL.
    # This prevents Spotify and YouTube cards with the same country/view from sharing data.
    cache_key = f"{platform.lower()}::{view.lower()}::{(country or '').lower()}::{source_url}"

    now = datetime.utcnow()
    cached = CACHE.get(cache_key)

    if cached and not refresh:
        age = now - cached["fetched_at_dt"]
        if age.total_seconds() < CACHE_TTL_SECONDS:
            return cached["payload"]

    html = fetch_html(source_url)
    rows = parse_html(html, platform.lower(), limit)

    payload = {
        "platform": platform.lower(),
        "view": view.lower(),
        "country": (country or "").lower(),
        "title": source["title"],
        "source_url": source_url,
        "fetched_at": now.isoformat() + "Z",
        "rows": rows,
    }

    CACHE[cache_key] = {
        "fetched_at_dt": now,
        "payload": payload,
    }

    return payload


@router.get("/chart")
def get_chart(
    platform: str = Query(default="spotify", pattern="^(spotify|youtube|aggregate|tiktok)$"),
    view: str = Query(default="weekly_country"),
    country: str = Query(default="us"),
    limit: int = Query(default=100, ge=1, le=500),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        payload = fetch_chart(platform, view, country, limit, refresh)
        payload = dict(payload)
        payload["cached"] = not refresh
        return payload
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch chart source: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Trends route error: {type(exc).__name__}: {exc}") from exc


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
