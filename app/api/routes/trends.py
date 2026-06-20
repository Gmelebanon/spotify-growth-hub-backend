import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Session

from app.core.database import Base, get_db


router = APIRouter(prefix="/api/trends", tags=["Trends"])

CACHE_TTL_SECONDS = 30 * 60

CHARTEX_API_BASE_URL = os.getenv("CHARTEX_API_BASE_URL", "https://api.chartex.com")
CHARTEX_API_KEY = os.getenv("CHARTEX_API_KEY", "")
CHARTEX_APP_ID = os.getenv("CHARTEX_APP_ID", "")
CHARTEX_APP_TOKEN = os.getenv("CHARTEX_APP_TOKEN", "")
CHARTEX_MIN_VALUE = int(os.getenv("CHARTEX_MIN_VALUE", "0"))


class TikTokTrendRow(Base):
    __tablename__ = "tiktok_trend_rows"

    id = Column(Integer, primary_key=True, index=True)
    country = Column(String(64), nullable=False, index=True)
    country_label = Column(String(80), nullable=False)
    view = Column(String(40), nullable=False, index=True)
    period = Column(String(24), nullable=False)
    position = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=True)
    creates = Column(Integer, nullable=True)
    source = Column(String(80), nullable=False, default="chartex")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    raw_json = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("country", "view", "position", name="uq_tiktok_trends_country_view_position"),
    )





class SocialTrendTodoItem(Base):
    __tablename__ = "social_trend_todo_items"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(40), nullable=False, index=True)
    card_title = Column(String(120), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    is_done = Column(Boolean, nullable=False, default=False)
    done_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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


AGGREGATE_COUNTRY_ORDER = {
    "US": 1,
    "UK": 2,
    "Australia": 3,
    "Germany": 4,
    "France": 5,
    "Brazil": 6,
    "Spain": 7,
    "Italy": 8,
}

AGGREGATE_ALLOWED_COUNTRIES = {
    "United States": "US",
    "United Kingdom": "UK",
    "Australia": "Australia",
    "Germany": "Germany",
    "France": "France",
    "Brazil": "Brazil",
    "Spain": "Spain",
    "Italy": "Italy",
}

TIKTOK_COUNTRIES: dict[str, dict[str, str]] = {
    "worldwide": {"name": "Worldwide", "slug": "worldwide", "code": ""},
    "global": {"name": "Worldwide", "slug": "worldwide", "code": ""},
    "us": {"name": "US", "slug": "united-states", "code": "US"},
    "gb": {"name": "UK", "slug": "united-kingdom", "code": "GB"},
    "au": {"name": "Australia", "slug": "australia", "code": "AU"},
    "de": {"name": "Germany", "slug": "germany", "code": "DE"},
    "fr": {"name": "France", "slug": "france", "code": "FR"},
    "br": {"name": "Brazil", "slug": "brazil", "code": "BR"},
    "es": {"name": "Spain", "slug": "spain", "code": "ES"},
    "it": {"name": "Italy", "slug": "italy", "code": "IT"},
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
        if view == "global_trending_weekly":
            return {
                "title": "YouTube Global Weekly",
                "url": "https://kworb.net/youtube/trending.html",
            }

        if view == "global_daily":
            return {
                "title": "YouTube Global Daily",
                "url": "https://kworb.net/youtube/realtime_anglo.html",
            }

        if view == "us_weekly":
            return {
                "title": "YouTube US Weekly",
                "url": "https://kworb.net/youtube/insights/us.html",
            }

        if view == "us_trending_daily":
            return {
                "title": "YouTube US Daily",
                "url": "https://kworb.net/youtube/trending/us.html",
            }

        code = country_info["youtube"]

        if code == "global":
            raise HTTPException(status_code=400, detail="Use global_trending_weekly or global_daily for YouTube global charts.")

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
    if len(cells) < 2:
        return None

    position = parse_int(cells[0])
    if position is None:
        return None

    ignored = {
        "pos",
        "position",
        "artist and title",
        "video",
        "title",
        "views",
        "streams",
        "country",
        "daily",
        "weekly",
    }

    title_cell = ""
    title_index = 1

    for index, cell in enumerate(cells[1:], start=1):
        cleaned = clean_text(cell)
        if not cleaned:
            continue

        lowered = cleaned.lower()
        if lowered in ignored:
            continue

        # Skip pure numeric metric cells.
        if parse_int(cleaned) is not None and re.fullmatch(r"[\d,\\s+\\-]+", cleaned):
            continue

        # Skip simple movement cells.
        if cleaned in {"=", "+", "-", "NEW", "RE"}:
            continue

        title_cell = cleaned
        title_index = index
        break

    if not title_cell:
        return None

    artist, title = split_artist_title(title_cell)
    if not title:
        title = title_cell

    numeric_values = [parse_int(cell) for cell in cells]
    numeric_present = [value for value in numeric_values[1:] if value is not None]

    metric_value = numeric_present[-1] if numeric_present else None
    metric_change = None

    if len(numeric_present) >= 2:
        metric_value = numeric_present[-2]
        metric_change = numeric_present[-1]

    return {
        "position": position,
        "position_change": clean_text(cells[1]) if len(cells) > 1 and title_index != 1 else "=",
        "artist": artist,
        "title": title,
        "metric_label": "Views",
        "metric_value": metric_value,
        "metric_change": metric_change,
        "extra_1_label": "Source",
        "extra_1_value": "YouTube",
        "extra_2_label": "Scope",
        "extra_2_value": "Global/US",
        "total_label": "Total",
        "total_value": None,
        "raw": cells,
    }


def aggregate_country_alias(value: str) -> str | None:
    cleaned = clean_text(value)

    aliases = {
        "United States": "US",
        "US": "US",
        "USA": "US",
        "United Kingdom": "UK",
        "UK": "UK",
        "Australia": "Australia",
        "Germany": "Germany",
        "France": "France",
        "Brazil": "Brazil",
        "Spain": "Spain",
        "Italy": "Italy",
    }

    return aliases.get(cleaned)


def make_aggregate_row(cells: list[str]) -> dict[str, Any] | None:
    if len(cells) < 7:
        return None

    display_country = aggregate_country_alias(cells[0])
    if not display_country:
        return None

    return {
        "position": AGGREGATE_COUNTRY_ORDER.get(display_country, 999),
        "position_change": "",
        "artist": "",
        "title": display_country,
        "metric_label": "Aggregate",
        "metric_value": None,
        "metric_change": None,
        "extra_1_label": "iTunes",
        "extra_1_value": clean_text(cells[1]) if len(cells) > 1 else "",
        "extra_2_label": "Spotify",
        "extra_2_value": clean_text(cells[2]) if len(cells) > 2 else "",
        "total_label": "YouTube",
        "total_value": None,
        "country": display_country,
        "itunes": clean_text(cells[1]) if len(cells) > 1 else "",
        "spotify": clean_text(cells[2]) if len(cells) > 2 else "",
        "apple_music": clean_text(cells[3]) if len(cells) > 3 else "",
        "youtube": clean_text(cells[4]) if len(cells) > 4 else "",
        "shazam": clean_text(cells[5]) if len(cells) > 5 else "",
        "deezer": clean_text(cells[6]) if len(cells) > 6 else "",
        "raw": cells,
    }


def parse_aggregate_html(html: str, limit: int) -> list[dict[str, Any]]:
    parser = KworbTableParser()
    parser.feed(html)

    rows: list[dict[str, Any]] = []

    for cells in parser.rows:
        record = make_aggregate_row(cells)
        if record:
            rows.append(record)

    # Fallback for pages where rows are exposed in rendered text but not captured
    # by the simple table parser.
    if not rows:
        # Remove scripts/styles and convert tags to line breaks so country rows
        # and song cells remain parseable.
        cleaned_html = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        cleaned_html = re.sub(r"<style.*?</style>", " ", cleaned_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "\n", cleaned_html)
        lines = [clean_text(line) for line in text.splitlines()]
        lines = [line for line in lines if line]

        allowed_full_names = [
            "United States",
            "United Kingdom",
            "Australia",
            "Germany",
            "France",
            "Brazil",
            "Spain",
            "Italy",
        ]

        for index, line in enumerate(lines):
            display_country = aggregate_country_alias(line)
            if not display_country or line not in allowed_full_names:
                continue

            values: list[str] = []
            pointer = index + 1
            while pointer < len(lines) and len(values) < 6:
                candidate = lines[pointer]
                pointer += 1

                if aggregate_country_alias(candidate):
                    break

                if candidate.lower() in {
                    "country",
                    "itunes",
                    "spotify",
                    "apple music",
                    "youtube",
                    "shazam",
                    "deezer",
                    "main countries:",
                    "other countries:",
                }:
                    continue

                values.append(candidate)

            if len(values) >= 6:
                record = make_aggregate_row([line, *values[:6]])
                if record:
                    rows.append(record)

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        country = str(row.get("country", ""))
        if country:
            deduped[country] = row

    ordered = sorted(
        deduped.values(),
        key=lambda row: AGGREGATE_COUNTRY_ORDER.get(str(row.get("country", "")), 999),
    )

    return ordered[:limit]


def aggregate_record(cells: list[str]) -> dict[str, Any] | None:
    return make_aggregate_row(cells)


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



class ChartexTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[list[str]] = []
        self.rows: list[list[list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []
        elif self.in_cell and tag in {"br", "p", "div"}:
            self.current_cell.append("\\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "span", "p", "div"} and self.in_cell:
            self.current_cell.append("\\n")
        elif tag in {"td", "th"} and self.in_cell:
            parts = [clean_text(part) for part in " ".join(self.current_cell).split("\\n")]
            self.current_row.append([part for part in parts if part])
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


def first_non_empty(parts: list[str]) -> str:
    for part in parts:
        cleaned = clean_text(part)
        if cleaned:
            return cleaned
    return ""


def chartex_song_title_artist_from_parts(parts: list[str]) -> tuple[str, str]:
    cleaned = [clean_text(part) for part in parts if clean_text(part)]

    if not cleaned:
        return "", ""

    # Chartex song cells usually render title first and artist second.
    title = cleaned[0]
    artist = cleaned[1] if len(cleaned) > 1 else ""

    if " - " in title and not artist:
        artist, title = split_artist_title(title)

    return title, artist


def parse_chartex_tiktok_table(html: str, limit: int) -> list[dict[str, Any]]:
    parser = ChartexTableParser()
    parser.feed(html)

    rows: list[dict[str, Any]] = []

    for row in parser.rows:
        flat = [first_non_empty(cell) for cell in row]
        if len(flat) < 3:
            continue

        position = parse_int(flat[0])
        if position is None or position < 1 or position > 500:
            continue

        # Header row guard.
        joined = " ".join(flat).lower()
        if "sound name on tiktok" in joined or "song title" in joined:
            continue

        # Chartex visible columns:
        # Rank | Sound name on TikTok | Song Title | Label / Distributor | ...
        song_parts = row[2] if len(row) > 2 else []
        sound_parts = row[1] if len(row) > 1 else []

        title, artist = chartex_song_title_artist_from_parts(song_parts)

        # If Chartex has no matched song title, fall back to the sound name.
        if not title:
            title, artist = chartex_song_title_artist_from_parts(sound_parts)

        if not title or is_bad_tiktok_title(title):
            continue

        rows.append({
            "position": position,
            "position_change": "=",
            "artist": artist,
            "title": title,
            "metric_label": "Creates",
            "metric_value": parse_int(flat[6] if len(flat) > 6 else None),
            "metric_change": None,
            "extra_1_label": "Source",
            "extra_1_value": "Chartex",
            "extra_2_label": "Platform",
            "extra_2_value": "TikTok",
            "total_label": "Total",
            "total_value": parse_int(flat[7] if len(flat) > 7 else None),
            "raw": flat,
        })

    return rows[:limit]


def tiktok_record(cells: list[str]) -> dict[str, Any] | None:
    if len(cells) < 3:
        return None

    position = parse_int(cells[0])
    if position is None or position < 1 or position > 500:
        return None

    joined = " ".join(cells).lower()
    if "sound name on tiktok" in joined or "song title" in joined:
        return None

    # Chartex table format:
    # Rank | Sound name on TikTok | Song Title | Label / Distributor | ...
    title_source = clean_text(cells[2]) if len(cells) > 2 else clean_text(cells[1])
    fallback_source = clean_text(cells[1]) if len(cells) > 1 else ""

    title = title_source
    artist = ""

    if " - " in title:
        artist, title = split_artist_title(title)

    if is_bad_tiktok_title(title):
        title = fallback_source
        artist = ""

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
    # First parse the visible Chartex table. This is the trusted source for:
    # /tiktok/songs/24-hours/worldwide
    # /tiktok/songs/7-days/worldwide
    # /tiktok/songs/24-hours/united-states
    # /tiktok/songs/7-days/united-states
    chartex_rows = parse_chartex_tiktok_table(html, limit)
    if chartex_rows:
        return chartex_rows[:limit]

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
    if platform == "aggregate":
        return parse_aggregate_html(html, limit)

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



def tiktok_period_for_view(view: str) -> str:
    return "7-days" if view in {"weekly_country", "us_weekly"} else "1-day"


def normalize_tiktok_view(view: str) -> str:
    if view in {"us_weekly", "weekly", "global_weekly"}:
        return "weekly_country"
    if view in {"us_daily", "daily", "global_daily"}:
        return "daily_country"
    return view


def chartex_songs_url() -> str:
    return f"{CHARTEX_API_BASE_URL.rstrip('/')}/external/v1/songs/"


def chartex_sounds_url() -> str:
    return f"{CHARTEX_API_BASE_URL.rstrip('/')}/external/v1/tiktok-sounds/"




def chartex_web_period_for_view(view: str) -> str:
    normalized_view = normalize_tiktok_view(view)
    return "24-hours" if normalized_view == "daily_country" else "7-days"


def chartex_web_country_slug(country: str) -> str:
    normalized_country = country.lower().strip()

    if normalized_country in {"global", "worldwide"}:
        return "worldwide"

    country_info = TIKTOK_COUNTRIES.get(normalized_country)

    if country_info:
        return country_info.get("slug", normalized_country)

    return normalized_country


def chartex_tiktok_web_url(view: str, country: str) -> str:
    return (
        "https://chartex.com/tiktok/songs/"
        f"{chartex_web_period_for_view(view)}/"
        f"{chartex_web_country_slug(country)}"
    )


def chartex_spotify_web_url(view: str, country: str) -> str:
    country_slug = "worldwide" if country.lower().strip() in {"global", "worldwide"} else country.lower().strip()
    return (
        "https://chartex.com/spotify/songs/"
        f"{chartex_web_period_for_view(view)}/"
        f"{country_slug}"
    )

def chartex_sort_by_for_view(view: str) -> str:
    normalized_view = normalize_tiktok_view(view)
    if normalized_view == "daily_country":
        return "tiktok_last_24_hours_video_count"
    return "tiktok_last_7_days_video_count"


def chartex_song_params(country_code: str) -> dict[str, Any]:
    # Songs Chart is the best match for "TikTok creations" because the docs say
    # total_sound_count is available for TikTok songs and country_codes can filter.
    params: dict[str, Any] = {
        "sort_platform": "tiktok",
        "sort_column": "total_sound_count",
        "page": 1,
        "limit": 100,
    }

    if country_code:
        params["country_codes"] = country_code
        params["country_code"] = country_code
        params["country"] = country_code

    if CHARTEX_MIN_VALUE > 0:
        params["min_tiktok_sounds_count"] = CHARTEX_MIN_VALUE

    return params


def chartex_sound_params(view: str, country_code: str) -> dict[str, Any]:
    # Fallback if the Songs endpoint returns no rows.
    params: dict[str, Any] = {
        "sort_by": chartex_sort_by_for_view(view),
        "page": 1,
        "limit": 100,
    }

    if country_code:
        params["country_codes"] = country_code
        params["country_code"] = country_code
        params["country"] = country_code

    if CHARTEX_MIN_VALUE > 0:
        params["min_value"] = CHARTEX_MIN_VALUE

    return params


def chartex_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "NerdEngineTrends/1.0",
    }

    # Chartex may provide either a single API key or an App ID + App Token pair.
    # Keep multiple common header names so this works with the most common API-gateway styles.
    if CHARTEX_API_KEY:
        headers["Authorization"] = f"Bearer {CHARTEX_API_KEY}"
        headers["x-api-key"] = CHARTEX_API_KEY

    if CHARTEX_APP_ID:
        headers["x-app-id"] = CHARTEX_APP_ID
        headers["app-id"] = CHARTEX_APP_ID
        headers["X-App-Id"] = CHARTEX_APP_ID

    if CHARTEX_APP_TOKEN:
        headers["x-app-token"] = CHARTEX_APP_TOKEN
        headers["app-token"] = CHARTEX_APP_TOKEN
        headers["X-App-Token"] = CHARTEX_APP_TOKEN

        # Some APIs expect the token as a bearer token even when they call it an app token.
        if not CHARTEX_API_KEY:
            headers["Authorization"] = f"Bearer {CHARTEX_APP_TOKEN}"

    return headers


def value_from_keys(data: dict[str, Any], keys: list[str]) -> Any:
    lower = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        if key.lower() in lower and lower[key.lower()] not in {None, ""}:
            return lower[key.lower()]
    return None


def first_nested_string(value: Any, keys: list[str]) -> str:
    if isinstance(value, dict):
        found = value_from_keys(value, keys)
        if found not in {None, ""}:
            return clean_text(str(found))

        for child in value.values():
            nested = first_nested_string(child, keys)
            if nested:
                return nested

    elif isinstance(value, list):
        for child in value:
            nested = first_nested_string(child, keys)
            if nested:
                return nested

    return ""


def first_nested_int(value: Any, keys: list[str]) -> int | None:
    if isinstance(value, dict):
        found = value_from_keys(value, keys)
        parsed = parse_int(str(found)) if found is not None else None
        if parsed is not None:
            return parsed

        for child in value.values():
            nested = first_nested_int(child, keys)
            if nested is not None:
                return nested

    elif isinstance(value, list):
        for child in value:
            nested = first_nested_int(child, keys)
            if nested is not None:
                return nested

    return None


def collect_candidate_lists(value: Any, lists: list[list[dict[str, Any]]]) -> None:
    if isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        if dict_items:
            lists.append(dict_items)

        for item in value:
            collect_candidate_lists(item, lists)

    elif isinstance(value, dict):
        for child in value.values():
            collect_candidate_lists(child, lists)


def tiktok_item_to_row(item: dict[str, Any], fallback_position: int) -> dict[str, Any] | None:
    # Chartex Songs endpoint can return nested song/artist/platform objects.
    # Chartex TikTok Sounds endpoint can return nested sound/song/creator objects.
    title = first_nested_string(
        item,
        [
            "title",
            "song_title",
            "songTitle",
            "track_title",
            "trackTitle",
            "song_name",
            "songName",
            "sound_title",
            "soundTitle",
            "sound_name",
            "soundName",
            "music_title",
            "musicTitle",
            "name",
        ],
    )

    artist = first_nested_string(
        item,
        [
            "artist",
            "artist_name",
            "artistName",
            "artists",
            "author",
            "author_name",
            "authorName",
            "creator",
            "creator_name",
            "performer",
            "owner",
        ],
    )

    if not title:
        return None

    # Avoid rows where the API object name is a wrapper/list label.
    if title.lower() in {"results", "data", "song", "sound", "artist"}:
        return None

    position = first_nested_int(
        item,
        ["rank", "position", "chart_position", "chartPosition", "current_rank", "currentRank"],
    )
    if position is None:
        position = fallback_position

    creates = first_nested_int(
        item,
        [
            "total_sound_count",
            "tiktok_total_video_count",
            "tiktok_last_7_days_video_count",
            "tiktok_last_24_hours_video_count",
            "tiktok_video_count",
            "sound_count",
            "sounds_count",
            "video_count",
            "videos_count",
            "count",
            "value",
        ],
    )

    return {
        "position": position,
        "title": title,
        "artist": artist,
        "creates": creates,
        "raw": item,
    }


def extract_chartex_rows(payload: Any) -> list[dict[str, Any]]:
    candidate_lists: list[list[dict[str, Any]]] = []
    collect_candidate_lists(payload, candidate_lists)

    best_rows: list[dict[str, Any]] = []

    for candidate_list in candidate_lists:
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(candidate_list, start=1):
            row = tiktok_item_to_row(item, index)
            if row:
                rows.append(row)

        if len(rows) > len(best_rows):
            best_rows = rows

    deduped: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in best_rows:
        key = (
            int(row.get("position") or 0),
            str(row.get("title") or ""),
            str(row.get("artist") or ""),
        )
        if key[0] > 0 and key[1]:
            deduped[key] = row

    return sorted(deduped.values(), key=lambda item: int(item.get("position") or 999999))


def payload_debug_summary(payload: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "payload_type": type(payload).__name__,
    }

    if isinstance(payload, dict):
        summary["top_level_keys"] = list(payload.keys())[:30]
        for key in ["results", "data", "items", "songs", "sounds"]:
            value = payload.get(key)
            if isinstance(value, list):
                summary[f"{key}_length"] = len(value)
                if value and isinstance(value[0], dict):
                    summary[f"{key}_first_keys"] = list(value[0].keys())[:40]
                break

    if isinstance(payload, list):
        summary["list_length"] = len(payload)
        if payload and isinstance(payload[0], dict):
            summary["first_item_keys"] = list(payload[0].keys())[:40]

    return summary


def serialize_tiktok_row(row: TikTokTrendRow) -> dict[str, Any]:
    return {
        "position": row.position,
        "position_change": "=",
        "artist": row.artist or "",
        "title": row.title,
        "metric_label": "Creates",
        "metric_value": row.creates,
        "metric_change": None,
        "extra_1_label": "Source",
        "extra_1_value": "Chartex DB",
        "extra_2_label": "Scope",
        "extra_2_value": row.country_label,
        "total_label": "Total",
        "total_value": None,
        "country": row.country,
        "view": row.view,
        "fetched_at": row.fetched_at.isoformat() + "Z" if row.fetched_at else None,
        "raw": [],
    }


def get_tiktok_chart_from_db(
    db: Session,
    view: str,
    country: str | None,
    limit: int,
) -> dict[str, Any]:
    normalized_view = normalize_tiktok_view(view)
    normalized_country = (country or "worldwide").lower().strip()

    if normalized_country == "global":
        normalized_country = "worldwide"

    if normalized_country not in TIKTOK_COUNTRIES:
        raise HTTPException(status_code=400, detail="Unsupported TikTok country.")

    country_info = TIKTOK_COUNTRIES[normalized_country]

    rows = (
        db.query(TikTokTrendRow)
        .filter(TikTokTrendRow.country == normalized_country)
        .filter(TikTokTrendRow.view == normalized_view)
        .order_by(TikTokTrendRow.position.asc())
        .limit(limit)
        .all()
    )

    latest = rows[0].fetched_at if rows else datetime.utcnow()

    return {
        "platform": "tiktok",
        "view": normalized_view,
        "country": normalized_country,
        "title": f"TikTok Creations - {country_info['name']}",
        "source_url": "database:tiktok_trend_rows",
        "fetched_at": latest.isoformat() + "Z",
        "rows": [serialize_tiktok_row(row) for row in rows],
    }


def sync_tiktok_chart(
    db: Session,
    view: str,
    country: str,
) -> dict[str, Any]:
    normalized_view = normalize_tiktok_view(view)
    normalized_country = country.lower().strip()

    if normalized_country == "global":
        normalized_country = "worldwide"

    if normalized_country not in TIKTOK_COUNTRIES:
        raise HTTPException(status_code=400, detail="Unsupported TikTok country.")

    country_info = TIKTOK_COUNTRIES[normalized_country]
    period = tiktok_period_for_view(normalized_view)
    country_code = country_info.get("code", "")

    attempts: list[dict[str, Any]] = []

    # Use the exact Chartex web chart URL only, so cards do not fall back to
    # the same generic API order.
    request_plan = [
        ("web", chartex_tiktok_web_url(normalized_view, normalized_country), {}, "html"),
    ]

    rows: list[dict[str, Any]] = []
    selected_payload: Any = None
    selected_source = ""
    selected_url = ""
    selected_params: dict[str, Any] = {}

    for source_name, url, params, response_type in request_plan:
        response = requests.get(url, headers=chartex_headers(), params=params, timeout=30)
        response.raise_for_status()

        if response_type == "html":
            payload = response.text
            extracted_rows = parse_tiktok_html(payload, 100)
            debug = {
                "payload_type": "html",
                "html_length": len(payload),
                "source_url": url,
            }
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise HTTPException(status_code=502, detail="Chartex API did not return JSON.") from exc

            extracted_rows = extract_chartex_rows(payload)
            debug = payload_debug_summary(payload)

        attempts.append({
            "source": source_name,
            "url": url,
            "params": params,
            "response_type": response_type,
            "rows_found": len(extracted_rows),
            "debug": debug,
        })

        if extracted_rows:
            rows = extracted_rows
            selected_payload = payload
            selected_source = source_name
            selected_url = url
            selected_params = params
            break

        # Keep the last payload for debugging even if no rows were found.
        selected_payload = payload
        selected_source = source_name
        selected_url = url
        selected_params = params

    if not rows:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Could not extract TikTok rows from the Chartex web chart.",
                "source_url": chartex_tiktok_web_url(normalized_view, normalized_country),
                "attempts": attempts,
            },
        )

    now = datetime.utcnow()

    # Replace the chart snapshot only after valid rows were extracted.
    (
        db.query(TikTokTrendRow)
        .filter(TikTokTrendRow.country == normalized_country)
        .filter(TikTokTrendRow.view == normalized_view)
        .delete(synchronize_session=False)
    )

    import json

    saved = 0
    for item in rows:
        title = clean_text(str(item.get("title") or ""))
        if not title:
            continue

        db.add(
            TikTokTrendRow(
                country=normalized_country,
                country_label=country_info["name"],
                view=normalized_view,
                period=period,
                position=int(item.get("position") or saved + 1),
                title=title,
                artist=clean_text(str(item.get("artist") or "")),
                creates=item.get("creates") if isinstance(item.get("creates"), int) else None,
                source=f"chartex:{selected_source}",
                fetched_at=now,
                raw_json=json.dumps(item.get("raw") or item, ensure_ascii=False)[:15000],
            )
        )
        saved += 1

    db.commit()

    return {
        "country": normalized_country,
        "country_label": country_info["name"],
        "view": normalized_view,
        "period": period,
        "source": selected_source,
        "url": selected_url,
        "params": selected_params,
        "saved_rows": saved,
        "attempts": attempts,
        "fetched_at": now.isoformat() + "Z",
    }


def tiktok_sync_targets(country: str | None = None) -> list[tuple[str, str]]:
    if country and country.lower().strip() not in {"", "all"}:
        normalized_country = country.lower().strip()
        if normalized_country == "global":
            normalized_country = "worldwide"
        return [
            ("weekly_country", normalized_country),
            ("daily_country", normalized_country),
        ]

    return [
        ("weekly_country", "worldwide"),
        ("daily_country", "worldwide"),
        ("weekly_country", "us"),
        ("daily_country", "us"),
    ]


def fetch_chart(platform: str, view: str, country: str | None, limit: int, refresh: bool, db: Session | None = None) -> dict[str, Any]:
    if platform.lower().strip() == "tiktok":
        if db is None:
            raise HTTPException(status_code=500, detail="Database session is required for TikTok trends.")
        return get_tiktok_chart_from_db(db, view, country, limit)

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





def ensure_social_trend_todo_schema(db: Session) -> None:
    bind = db.get_bind()
    SocialTrendTodoItem.__table__.create(bind=bind, checkfirst=True)
    dialect = bind.dialect.name

    if dialect == "postgresql":
        db.execute(text("ALTER TABLE IF EXISTS social_trend_todo_items ADD COLUMN IF NOT EXISTS is_done BOOLEAN NOT NULL DEFAULT FALSE"))
        db.execute(text("ALTER TABLE IF EXISTS social_trend_todo_items ADD COLUMN IF NOT EXISTS done_at TIMESTAMP"))
        db.commit()
        return

    try:
        db.execute(text("ALTER TABLE social_trend_todo_items ADD COLUMN is_done BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()
    except Exception:
        db.rollback()

    try:
        db.execute(text("ALTER TABLE social_trend_todo_items ADD COLUMN done_at TIMESTAMP"))
        db.commit()
    except Exception:
        db.rollback()


def serialize_social_trend_todo_item(item: SocialTrendTodoItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "platform": item.platform,
        "card_title": item.card_title,
        "position": item.position,
        "title": item.title,
        "artist": item.artist,
        "is_done": bool(getattr(item, "is_done", False)),
        "done_at": item.done_at.isoformat() + "Z" if getattr(item, "done_at", None) else "",
        "created_at": item.created_at.isoformat() + "Z" if item.created_at else "",
    }


@router.get("/todo")
def get_social_trends_todo(
    include_done: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_social_trend_todo_schema(db)

    query = db.query(SocialTrendTodoItem)

    if not include_done:
        query = query.filter(SocialTrendTodoItem.is_done == False)  # noqa: E712

    rows = (
        query
        .order_by(SocialTrendTodoItem.is_done.asc(), SocialTrendTodoItem.created_at.desc(), SocialTrendTodoItem.id.desc())
        .all()
    )

    return {"items": [serialize_social_trend_todo_item(row) for row in rows]}


@router.post("/todo")
def add_social_trends_todo(
    payload: dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_social_trend_todo_schema(db)
    raw_items = payload.get("items", [])

    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="items must be a list")

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        title = str(raw_item.get("title") or "").strip()
        artist = str(raw_item.get("artist") or "").strip()

        if not title:
            continue

        existing = (
            db.query(SocialTrendTodoItem)
            .filter(SocialTrendTodoItem.title == title)
            .filter(SocialTrendTodoItem.artist == artist)
            .filter(SocialTrendTodoItem.is_done == False)  # noqa: E712
            .first()
        )

        if existing:
            continue

        row = SocialTrendTodoItem(
            platform=str(raw_item.get("platform") or "").strip()[:40],
            card_title=str(raw_item.get("cardTitle") or raw_item.get("card_title") or "").strip()[:120],
            position=int(raw_item.get("position") or 0) if str(raw_item.get("position") or "0").isdigit() else 0,
            title=title,
            artist=artist or "-",
        )
        db.add(row)

    db.commit()

    return get_social_trends_todo(include_done=False, db=db)



@router.post("/todo/{item_id}/done")
def mark_social_trends_todo_done(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    ensure_social_trend_todo_schema(db)

    row = db.query(SocialTrendTodoItem).filter(SocialTrendTodoItem.id == item_id).first()

    if not row:
        raise HTTPException(status_code=404, detail="Todo item not found")

    row.is_done = True
    row.done_at = datetime.utcnow()
    db.commit()

    return {"ok": True, "item": serialize_social_trend_todo_item(row)}


@router.post("/todo/bulk-done")
def bulk_mark_social_trends_todo_done(
    payload: dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_social_trend_todo_schema(db)

    ids = payload.get("ids", [])
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")

    clean_ids = [int(item_id) for item_id in ids if str(item_id).isdigit()]

    if not clean_ids:
        return {"ok": True, "updated": 0}

    rows = db.query(SocialTrendTodoItem).filter(SocialTrendTodoItem.id.in_(clean_ids)).all()

    for row in rows:
        row.is_done = True
        row.done_at = datetime.utcnow()

    db.commit()

    return {"ok": True, "updated": len(rows)}


@router.post("/todo/bulk-delete")
def bulk_delete_social_trends_todo(
    payload: dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_social_trend_todo_schema(db)

    ids = payload.get("ids", [])
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")

    clean_ids = [int(item_id) for item_id in ids if str(item_id).isdigit()]

    if not clean_ids:
        return {"ok": True, "deleted": 0}

    rows = db.query(SocialTrendTodoItem).filter(SocialTrendTodoItem.id.in_(clean_ids)).all()

    for row in rows:
        db.delete(row)

    db.commit()

    return {"ok": True, "deleted": len(rows)}


@router.delete("/todo/{item_id}")
def delete_social_trends_todo(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    ensure_social_trend_todo_schema(db)
    row = db.query(SocialTrendTodoItem).filter(SocialTrendTodoItem.id == item_id).first()

    if not row:
        raise HTTPException(status_code=404, detail="Todo item not found")

    db.delete(row)
    db.commit()

    return {"ok": True, "deleted_id": item_id}


@router.get("/chart")
def get_chart(
    platform: str = Query(default="spotify", pattern="^(spotify|youtube|aggregate|tiktok)$"),
    view: str = Query(default="weekly_country"),
    country: str = Query(default="us"),
    limit: int = Query(default=100, ge=1, le=500),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        payload = fetch_chart(platform, view, country, limit, refresh, db)
        payload = dict(payload)
        payload["cached"] = not refresh
        return payload
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch chart source: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Trends route error: {type(exc).__name__}: {exc}") from exc


@router.post("/tiktok/sync")
def sync_tiktok_trends(
    country: str = Query(default="all"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for view, target_country in tiktok_sync_targets(country):
        try:
            results.append(sync_tiktok_chart(db, view, target_country))
        except Exception as exc:
            errors.append({
                "country": target_country,
                "view": view,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "ok": len(errors) == 0,
        "synced": results,
        "errors": errors,
    }


@router.get("/tiktok/sync")
def sync_tiktok_trends_get(
    country: str = Query(default="all"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return sync_tiktok_trends(country=country, db=db)



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
