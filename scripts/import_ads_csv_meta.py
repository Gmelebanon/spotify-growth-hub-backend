"""
One-time importer for Ads CSV metadata.

Usage from the backend repo after copying app/data/ads_table_meta_seed.json:

    python scripts/import_ads_csv_meta.py

It updates:
- ads_playlist_settings.category
- ads_playlist_settings.genre
- ads_playlist_settings.country
- ads_playlist_settings.master_playlist

It also attempts to update ads_meta and playlists.genre when the Spotify playlist exists locally.
"""

import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is missing. Run this from the backend environment or set DATABASE_URL first.")

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "app" / "data" / "ads_table_meta_seed.json"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def column_exists(db, table, column):
    return db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :table
              AND column_name = :column
            LIMIT 1
            """
        ),
        {"table": table, "column": column},
    ).first() is not None


def table_exists(db, table):
    row = db.execute(text("SELECT to_regclass(:table) AS table_name"), {"table": table}).first()
    return bool(row and row.table_name)


def ensure_settings_table(db):
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ads_playlist_settings (
                id SERIAL PRIMARY KEY,
                playlist_id TEXT UNIQUE NOT NULL,
                playlist_name TEXT,
                account_name TEXT,
                genre TEXT,
                category TEXT,
                country TEXT,
                master_playlist TEXT,
                ad_date DATE,
                campaign_status TEXT,
                budget NUMERIC,
                followers INTEGER,
                last_synced TIMESTAMP,
                settings JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
    )


def find_playlist(db, spotify_playlist_id):
    parts = []
    if column_exists(db, "playlists", "spotify_id"):
        parts.append("spotify_id = :spotify_playlist_id")
    if column_exists(db, "playlists", "spotify_playlist_id"):
        parts.append("spotify_playlist_id = :spotify_playlist_id")
    if not parts:
        return None

    deleted_order = "CASE WHEN COALESCE(is_deleted, FALSE) = FALSE THEN 0 ELSE 1 END," if column_exists(db, "playlists", "is_deleted") else ""
    return db.execute(
        text(
            f"""
            SELECT id, name
            FROM playlists
            WHERE {" OR ".join(parts)}
            ORDER BY {deleted_order} id ASC
            LIMIT 1
            """
        ),
        {"spotify_playlist_id": spotify_playlist_id},
    ).first()


def main():
    rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    db = SessionLocal()

    try:
        ensure_settings_table(db)

        processed = 0
        matched = 0
        missing = 0

        for row in rows:
            spotify_playlist_id = clean(row.get("spotify_playlist_id"))
            if not spotify_playlist_id:
                continue

            processed += 1
            playlist = find_playlist(db, spotify_playlist_id)
            playlist_id_for_settings = spotify_playlist_id
            if playlist:
                matched += 1
                playlist_id_for_settings = str(playlist.id)
            else:
                missing += 1

            payload = {
                "category": clean(row.get("category")),
                "genre": clean(row.get("genre")),
                "country": clean(row.get("country")),
                "master_playlist": clean(row.get("master_playlist")),
            }
            settings_patch = {key: value for key, value in payload.items() if value is not None}

            db.execute(
                text(
                    """
                    INSERT INTO ads_playlist_settings (
                        playlist_id, playlist_name, account_name,
                        category, genre, country, master_playlist,
                        settings, updated_at
                    ) VALUES (
                        :playlist_id, :playlist_name, :account_name,
                        :category, :genre, :country, :master_playlist,
                        CAST(:settings AS JSONB), NOW()
                    )
                    ON CONFLICT (playlist_id) DO UPDATE SET
                        playlist_name = COALESCE(EXCLUDED.playlist_name, ads_playlist_settings.playlist_name),
                        account_name = COALESCE(EXCLUDED.account_name, ads_playlist_settings.account_name),
                        category = EXCLUDED.category,
                        genre = EXCLUDED.genre,
                        country = EXCLUDED.country,
                        master_playlist = EXCLUDED.master_playlist,
                        settings = COALESCE(ads_playlist_settings.settings, '{}'::jsonb) || EXCLUDED.settings,
                        updated_at = NOW()
                    """
                ),
                {
                    "playlist_id": playlist_id_for_settings,
                    "playlist_name": clean(row.get("playlist_name")),
                    "account_name": clean(row.get("account_name")),
                    **payload,
                    "settings": json.dumps(settings_patch),
                },
            )

            if playlist and table_exists(db, "ads_meta"):
                try:
                    db.execute(
                        text(
                            """
                            UPDATE ads_meta
                            SET category = :category,
                                genre = :genre,
                                country = :country,
                                master_playlist = :master_playlist
                            WHERE playlist_id = :playlist_id
                            """
                        ),
                        {"playlist_id": playlist.id, **payload},
                    )
                except Exception:
                    pass

        db.commit()
        print({
            "success": True,
            "processed": processed,
            "matched_playlists": matched,
            "missing_local_playlist": missing,
        })
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
