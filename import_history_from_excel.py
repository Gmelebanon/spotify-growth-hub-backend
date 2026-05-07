import re
from datetime import datetime, date
from pathlib import Path

from openpyxl import load_workbook

from app.core.database import SessionLocal
from app.models.playlist import Playlist
from app.models.follower_history import FollowerHistory

EXCEL_FILE = Path("Spotify_Master.xlsx")


def norm(value):
    return str(value or "").strip()


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = norm(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m", "%m/%d/%Y", "%m/%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            year = parsed.year if parsed.year != 1900 else datetime.utcnow().year
            return date(year, parsed.month, parsed.day)
        except ValueError:
            pass
    return None


def extract_spotify_id(value):
    text = norm(value)
    match = re.search(r"playlist/([A-Za-z0-9]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{16,}", text):
        return text
    return None


def main():
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(f"Missing {EXCEL_FILE}")

    db = SessionLocal()

    playlists = db.query(Playlist).all()

    by_name = {norm(p.name).lower(): p.id for p in playlists}
    by_spotify = {}

    for p in playlists:
        for value in [
            getattr(p, "spotify_id", None),
            getattr(p, "spotify_playlist_id", None),
            getattr(p, "spotify_url", None),
            getattr(p, "playlist_url", None),
            getattr(p, "url", None),
        ]:
            sid = extract_spotify_id(value)
            if sid:
                by_spotify[sid] = p.id

    existing = {
        (row.playlist_id, row.date)
        for row in db.query(FollowerHistory.playlist_id, FollowerHistory.date).all()
        if row.date
    }

    wb = load_workbook(EXCEL_FILE, data_only=True, read_only=True)

    to_insert = []
    skipped = 0

    for sheet in wb.worksheets:
        rows = sheet.iter_rows(values_only=True)
        headers_raw = next(rows, None)

        if not headers_raw:
            continue

        headers = [norm(h).lower().replace(" ", "_") for h in headers_raw]

        date_cols = {
            index: parse_date(value)
            for index, value in enumerate(headers_raw)
            if parse_date(value)
        }

        if not date_cols:
            continue

        for row in rows:
            row_data = {
                headers[i]: row[i]
                for i in range(min(len(headers), len(row)))
                if headers[i]
            }

            spotify_id = None
            for key in ["spotify_id", "spotify_playlist_id", "spotify_link", "playlist_link", "url", "link"]:
                spotify_id = extract_spotify_id(row_data.get(key))
                if spotify_id:
                    break

            playlist_id = by_spotify.get(spotify_id) if spotify_id else None

            if not playlist_id:
                name = (
                    row_data.get("title")
                    or row_data.get("playlist")
                    or row_data.get("playlist_name")
                    or row_data.get("name")
                )
                playlist_id = by_name.get(norm(name).lower())

            if not playlist_id:
                skipped += 1
                continue

            for col_index, day in date_cols.items():
                if col_index >= len(row):
                    continue

                value = row[col_index]
                if value is None or value == "":
                    continue

                try:
                    followers = int(float(value))
                except Exception:
                    continue

                key = (playlist_id, day)
                if key in existing:
                    continue

                existing.add(key)
                to_insert.append(
                    FollowerHistory(
                        playlist_id=playlist_id,
                        date=day,
                        followers=followers,
                        created_at=datetime.combine(day, datetime.min.time()),
                    )
                )

                if len(to_insert) >= 1000:
                    db.bulk_save_objects(to_insert)
                    db.commit()
                    print(f"Inserted batch. Total so far...")
                    to_insert.clear()

    if to_insert:
        db.bulk_save_objects(to_insert)
        db.commit()

    db.close()

    print("Import complete")
    print(f"Skipped unmatched rows: {skipped}")


if __name__ == "__main__":
    main()