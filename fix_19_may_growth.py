import csv
import os
from datetime import date, datetime
from sqlalchemy import text

from app.core.database import SessionLocal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_FILE = os.path.join(BASE_DIR, "may16_total_followers.csv")

DAY_17 = date(2026, 5, 17)
DAY_18 = date(2026, 5, 18)
DAY_19 = date(2026, 5, 19)
DAY_20 = date(2026, 5, 20)

# First run as True. After checking the preview, change to False.
DRY_RUN = False

# Anything higher than this is probably a total follower mistake, not daily growth.
MAX_REASONABLE_DAILY_GROWTH = 100


def load_16_totals():
    """
    Load 16/5 total follower baseline from may16_total_followers.csv.

    If duplicate rows exist for the same playlist_id, keep the row with the highest id.
    """
    if not os.path.exists(BASELINE_FILE):
        raise FileNotFoundError(
            f"Missing baseline file: {BASELINE_FILE}\n"
            "Place may16_total_followers.csv in the backend root folder."
        )

    latest_by_playlist = {}

    with open(BASELINE_FILE, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        required = {"playlist_id", "followers"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")

        for row in reader:
            try:
                playlist_id = int(float(row["playlist_id"]))
                followers = int(float(row["followers"] or 0))
                row_id = int(float(row.get("id") or 0))
            except Exception:
                continue

            existing = latest_by_playlist.get(playlist_id)
            if existing is None or row_id > existing["row_id"]:
                latest_by_playlist[playlist_id] = {
                    "row_id": row_id,
                    "followers": followers,
                }

    return {
        playlist_id: data["followers"]
        for playlist_id, data in latest_by_playlist.items()
    }


def get_daily_values(db, target_date):
    """
    Get latest daily-growth value per playlist for a date.
    follower_history.followers is daily growth in your current setup.
    """
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (playlist_id)
                playlist_id,
                followers
            FROM public.follower_history
            WHERE COALESCE(date, created_at::date) = :target_date
            ORDER BY playlist_id, created_at DESC, id DESC
        """),
        {"target_date": target_date},
    ).mappings().all()

    return {
        int(row["playlist_id"]): int(row["followers"] or 0)
        for row in rows
    }


def get_current_playlist_totals(db):
    """
    Current playlists.followers should be the latest Spotify total follower number.
    After your latest sync, this is treated as 20/5 total.
    """
    rows = db.execute(
        text("""
            SELECT id, followers
            FROM public.playlists
        """)
    ).mappings().all()

    return {
        int(row["id"]): int(row["followers"] or 0)
        for row in rows
    }


def get_existing_history_row_id(db, playlist_id, target_date):
    row = db.execute(
        text("""
            SELECT id
            FROM public.follower_history
            WHERE playlist_id = :playlist_id
              AND COALESCE(date, created_at::date) = :target_date
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """),
        {
            "playlist_id": playlist_id,
            "target_date": target_date,
        },
    ).mappings().first()

    return int(row["id"]) if row else None


def upsert_daily_growth(db, playlist_id, target_date, growth):
    """
    Update existing 19/5 row if it exists.
    Insert if missing.
    """
    existing_id = get_existing_history_row_id(db, playlist_id, target_date)
    now = datetime.utcnow()

    if existing_id:
        db.execute(
            text("""
                UPDATE public.follower_history
                SET followers = :growth,
                    date = :target_date,
                    created_at = :created_at
                WHERE id = :id
            """),
            {
                "id": existing_id,
                "growth": growth,
                "target_date": target_date,
                "created_at": now,
            },
        )
        return "updated"

    db.execute(
        text("""
            INSERT INTO public.follower_history
                (playlist_id, date, followers, created_at)
            VALUES
                (:playlist_id, :target_date, :growth, :created_at)
        """),
        {
            "playlist_id": playlist_id,
            "target_date": target_date,
            "growth": growth,
            "created_at": now,
        },
    )
    return "inserted"


def write_preview(rows_ready, suspicious):
    preview_path = os.path.join(BASE_DIR, "may19_growth_preview.csv")

    with open(preview_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "playlist_id",
                "total_16",
                "growth_17",
                "growth_18",
                "growth_19",
                "growth_20",
                "total_18",
                "total_19",
                "total_20",
                "was_suspicious",
                "reason",
            ],
        )
        writer.writeheader()

        suspicious_by_id = {
            item["playlist_id"]: item
            for item in suspicious
        }

        for row in rows_ready:
            suspicious_item = suspicious_by_id.get(row["playlist_id"])
            writer.writerow({
                "playlist_id": row["playlist_id"],
                "total_16": row["total_16"],
                "growth_17": row["growth_17"],
                "growth_18": row["growth_18"],
                "growth_19": row["growth_19"],
                "growth_20": row["growth_20"],
                "total_18": row["total_18"],
                "total_19": row["total_19"],
                "total_20": row["total_20"],
                "was_suspicious": "yes" if suspicious_item else "no",
                "reason": suspicious_item["reason"] if suspicious_item else "",
            })

    return preview_path


def main():
    print("Fixing 2026-05-19 daily growth")
    print(f"Mode: {'DRY RUN - no DB changes' if DRY_RUN else 'LIVE - will update database'}")
    print(f"Baseline file: {BASELINE_FILE}")
    print("")
    print("Calculation:")
    print("18/5 total = 16/5 total + 17/5 daily + 18/5 daily")
    print("19/5 total = current playlist total - 20/5 daily")
    print("19/5 daily = 19/5 total - 18/5 total")
    print("")

    totals_16 = load_16_totals()
    db = SessionLocal()

    try:
        growth_17 = get_daily_values(db, DAY_17)
        growth_18 = get_daily_values(db, DAY_18)
        growth_20 = get_daily_values(db, DAY_20)
        current_totals_20 = get_current_playlist_totals(db)

        rows_ready = []
        suspicious = []
        missing_current_totals = 0

        for playlist_id, total_16 in totals_16.items():
            if playlist_id not in current_totals_20:
                missing_current_totals += 1
                continue

            g17 = growth_17.get(playlist_id, 0)
            g18 = growth_18.get(playlist_id, 0)
            g20 = growth_20.get(playlist_id, 0)

            total_18 = total_16 + g17 + g18
            total_20 = current_totals_20[playlist_id]
            total_19 = total_20 - g20
            g19_raw = total_19 - total_18
            g19 = g19_raw

            if abs(g19_raw) > MAX_REASONABLE_DAILY_GROWTH:
                suspicious.append({
                    "playlist_id": playlist_id,
                    "total_16": total_16,
                    "growth_17": g17,
                    "growth_18": g18,
                    "growth_20": g20,
                    "total_18": total_18,
                    "total_19": total_19,
                    "total_20": total_20,
                    "calculated_19": g19_raw,
                    "reason": f"abs(growth_19) > {MAX_REASONABLE_DAILY_GROWTH}; clamped to 0",
                })
                g19 = 0

            rows_ready.append({
                "playlist_id": playlist_id,
                "total_16": total_16,
                "growth_17": g17,
                "growth_18": g18,
                "growth_19": g19,
                "growth_20": g20,
                "total_18": total_18,
                "total_19": total_19,
                "total_20": total_20,
            })

        preview_path = write_preview(rows_ready, suspicious)

        positives = [row for row in rows_ready if row["growth_19"] > 0]
        negatives = [row for row in rows_ready if row["growth_19"] < 0]
        zeros = [row for row in rows_ready if row["growth_19"] == 0]

        print(f"16/5 baselines loaded: {len(totals_16)}")
        print(f"Rows ready for 19/5: {len(rows_ready)}")
        print(f"Missing playlists in current DB: {missing_current_totals}")
        print(f"Positive 19/5 rows: {len(positives)}")
        print(f"Negative 19/5 rows: {len(negatives)}")
        print(f"Zero 19/5 rows: {len(zeros)}")
        print(f"Suspicious values clamped to 0: {len(suspicious)}")
        print(f"Preview written: {preview_path}")
        print("")

        print("Top calculated 19/5 values:")
        for row in sorted(rows_ready, key=lambda x: x["growth_19"], reverse=True)[:20]:
            print(
                f"playlist_id={row['playlist_id']} "
                f"19/5={row['growth_19']} "
                f"total_18={row['total_18']} "
                f"total_19={row['total_19']} "
                f"total_20={row['total_20']}"
            )

        if suspicious[:10]:
            print("")
            print("Sample suspicious rows:")
            for item in suspicious[:10]:
                print(item)

        if DRY_RUN:
            print("")
            print("Dry run complete. No database changes were saved.")
            print("Review may19_growth_preview.csv.")
            print("If it looks correct, change DRY_RUN = False and run again.")
            return

        inserted = 0
        updated = 0

        for row in rows_ready:
            action = upsert_daily_growth(
                db,
                row["playlist_id"],
                DAY_19,
                row["growth_19"],
            )

            if action == "inserted":
                inserted += 1
            else:
                updated += 1

        db.commit()

        print("")
        print(f"Done. Inserted: {inserted}, Updated: {updated}")

    except Exception as exc:
        db.rollback()
        print("Failed:", exc)
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()