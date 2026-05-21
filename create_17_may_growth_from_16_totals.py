import csv
import os
from datetime import datetime, date
from pathlib import Path
from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.playlist import Playlist

# Put the Supabase export / pasted text file here, next to this script.
# It must contain columns: id, playlist_id, date, followers, created_at
SOURCE_FILE = Path(os.getenv("MAY16_TOTALS_FILE", "may16_total_followers.csv"))
TARGET_DATE = date(2026, 5, 17)
PREVIOUS_TOTAL_DATE = date(2026, 5, 16)
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "y"}
ALLOW_NEGATIVE = os.getenv("ALLOW_NEGATIVE", "true").lower() in {"1", "true", "yes", "y"}


def parse_date(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def int_value(value, default=0):
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def detect_delimiter(sample: str):
    if "\t" in sample:
        return "\t"
    return ","


def load_may16_totals(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Export/paste the 16/5 totals into this file first."
        )

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    delimiter = detect_delimiter(text[:1000])

    rows_by_playlist = {}
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)

    for raw in reader:
        playlist_id = int_value(raw.get("playlist_id"), None)
        if not playlist_id:
            continue

        row_date = parse_date(raw.get("date"))
        if row_date != PREVIOUS_TOTAL_DATE:
            continue

        source_id = int_value(raw.get("id"), 0)
        followers_16 = int_value(raw.get("followers"), 0)

        existing = rows_by_playlist.get(playlist_id)
        # If duplicates exist for the same playlist/date, keep the latest Supabase row id.
        if existing is None or source_id > existing["source_id"]:
            rows_by_playlist[playlist_id] = {
                "playlist_id": playlist_id,
                "followers_16": followers_16,
                "source_id": source_id,
            }

    return rows_by_playlist


def main():
    print(f"Source file: {SOURCE_FILE.resolve()}")
    print(f"Mode: {'DRY RUN - no DB changes' if DRY_RUN else 'LIVE - will update database'}")
    print(f"Calculating {TARGET_DATE} daily growth using current playlists.followers - {PREVIOUS_TOTAL_DATE} total followers")

    totals_16 = load_may16_totals(SOURCE_FILE)
    if not totals_16:
        raise RuntimeError("No 16/5 totals found in the source file.")

    db = SessionLocal()
    try:
        playlist_ids = sorted(totals_16.keys())
        playlists = db.query(Playlist).filter(Playlist.id.in_(playlist_ids)).all()
        playlist_by_id = {p.id: p for p in playlists}

        rows_to_insert = []
        unmatched = []
        negatives = []

        for playlist_id in playlist_ids:
            playlist = playlist_by_id.get(playlist_id)
            if not playlist:
                unmatched.append(playlist_id)
                continue

            followers_16 = totals_16[playlist_id]["followers_16"]
            current_total = int_value(getattr(playlist, "followers", 0), 0)
            growth_17 = current_total - followers_16

            if growth_17 < 0:
                negatives.append((playlist_id, followers_16, current_total, growth_17))
                if not ALLOW_NEGATIVE:
                    growth_17 = 0

            rows_to_insert.append({
                "playlist_id": playlist_id,
                "date": TARGET_DATE.isoformat(),
                "followers": growth_17,
                "created_at": datetime.combine(TARGET_DATE, datetime.min.time()),
                "followers_16": followers_16,
                "current_total": current_total,
            })

        print(f"Loaded 16/5 totals: {len(totals_16)} unique playlists")
        print(f"Matched playlists in DB: {len(rows_to_insert)}")
        print(f"Unmatched playlist IDs: {len(unmatched)}")
        if unmatched[:20]:
            print("Sample unmatched:", unmatched[:20])
        print(f"Negative growth rows: {len(negatives)}")
        if negatives[:20]:
            print("Sample negatives playlist_id, total_16, current_total, growth:", negatives[:20])

        preview_file = Path("may17_growth_preview.csv")
        with preview_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["playlist_id", "date", "followers", "created_at", "followers_16", "current_total"],
            )
            writer.writeheader()
            writer.writerows(rows_to_insert)
        print(f"Preview written: {preview_file.resolve()}")

        if DRY_RUN:
            print("Dry run complete. No changes were saved.")
            return

        db.execute(
            text("""
                DELETE FROM public.follower_history
                WHERE COALESCE(date, created_at::date) = :target_date
            """),
            {"target_date": TARGET_DATE.isoformat()},
        )

        for row in rows_to_insert:
            db.execute(
                text("""
                    INSERT INTO public.follower_history (playlist_id, date, followers, created_at)
                    VALUES (:playlist_id, :date, :followers, :created_at)
                """),
                {
                    "playlist_id": row["playlist_id"],
                    "date": row["date"],
                    "followers": row["followers"],
                    "created_at": row["created_at"],
                },
            )

        db.commit()
        print(f"Done. Inserted {len(rows_to_insert)} rows for {TARGET_DATE}.")

    except Exception as exc:
        db.rollback()
        print("Failed:", exc)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
