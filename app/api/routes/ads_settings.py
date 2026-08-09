import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

router = APIRouter()


def _dict_rows(result):
    return [dict(row._mapping) for row in result]


def _clean_text(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _column_exists(db: Session, table_name: str, column_name: str) -> bool:
    try:
        row = db.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name
                  AND column_name = :column_name
                LIMIT 1
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).first()
        return row is not None
    except Exception:
        return False


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        row = db.execute(
            text("SELECT to_regclass(:table_name) AS table_name"),
            {"table_name": table_name},
        ).first()
        return bool(row and row.table_name)
    except Exception:
        return False


def _ensure_ads_playlist_settings_table(db: Session):
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

    db.execute(
        text(
            """
            ALTER TABLE ads_playlist_settings
            ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE
            """
        )
    )
    db.execute(
        text(
            """
            ALTER TABLE ads_playlist_settings
            ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL
            """
        )
    )


def _load_ads_csv_seed():
    seed_path = Path(__file__).resolve().parents[2] / "data" / "ads_table_meta_seed.json"
    if not seed_path.exists():
        raise HTTPException(status_code=500, detail=f"Missing seed file: {seed_path}")

    with seed_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise HTTPException(status_code=500, detail="ads_table_meta_seed.json must contain a list")

    return payload


def _normalize_seed_rows(seed_rows: list[dict]):
    """Clean, dedupe, and keep only rows with a Spotify playlist ID."""
    by_spotify_id = {}

    for row in seed_rows:
        spotify_playlist_id = _clean_text(row.get("spotify_playlist_id") or row.get("ID") or row.get("id"))
        if not spotify_playlist_id:
            continue

        by_spotify_id[spotify_playlist_id] = {
            "spotify_playlist_id": spotify_playlist_id,
            "playlist_name": _clean_text(row.get("playlist_name") or row.get("Playlist") or row.get("Name")),
            "account_name": _clean_text(row.get("account_name") or row.get("Account")),
            "category": _clean_text(row.get("category") or row.get("Category")),
            "genre": _clean_text(row.get("genre") or row.get("Genre")),
            "country": _clean_text(row.get("country") or row.get("Country")),
            "master_playlist": _clean_text(row.get("master_playlist") or row.get("Master")),
        }

    return list(by_spotify_id.values())


def _save_filter_options_bulk(db: Session, rows: list[dict]):
    if not rows or not _table_exists(db, "ads_filter_options"):
        return {"inserted_or_existing": 0}

    values = []
    for row in rows:
        if row.get("category"):
            values.append({"option_type": "category", "value": row["category"]})
        if row.get("genre"):
            values.append({"option_type": "genre", "value": row["genre"]})
        if row.get("country"):
            values.append({"option_type": "country", "value": row["country"]})
        if row.get("master_playlist"):
            values.append({"option_type": "master_playlist", "value": row["master_playlist"]})

    unique_values = list({(item["option_type"], item["value"]): item for item in values}.values())
    if not unique_values:
        return {"inserted_or_existing": 0}

    try:
        db.execute(
            text(
                """
                INSERT INTO ads_filter_options (option_type, value)
                SELECT option_type, value
                FROM jsonb_to_recordset(CAST(:rows AS JSONB))
                AS x(option_type TEXT, value TEXT)
                ON CONFLICT (option_type, value) DO NOTHING
                """
            ),
            {"rows": json.dumps(unique_values)},
        )
    except Exception:
        # Keep import successful even if old DB has no matching unique constraint.
        pass

    return {"inserted_or_existing": len(unique_values)}


@router.get("/api/ads/settings")
def get_ads_settings():
    db: Session = SessionLocal()
    try:
        _ensure_ads_playlist_settings_table(db)

        result = db.execute(
            text(
                """
                SELECT
                    s.id,
                    s.playlist_id,
                    s.playlist_name,
                    s.account_name,
                    s.genre,
                    s.category,
                    s.country,
                    s.master_playlist,
                    s.ad_date,
                    s.campaign_status,
                    s.budget,
                    s.followers,
                    s.last_synced,
                    s.settings,
                    s.created_at,
                    s.updated_at
                FROM ads_playlist_settings s
                LEFT JOIN playlists p
                    ON CAST(p.id AS TEXT) = CAST(s.playlist_id AS TEXT)
                    OR p.spotify_id = CAST(s.playlist_id AS TEXT)
                    OR p.spotify_playlist_id = CAST(s.playlist_id AS TEXT)
                WHERE COALESCE(s.is_deleted, FALSE) = FALSE
                  AND (
                    p.id IS NULL
                    OR COALESCE(p.is_deleted, FALSE) = FALSE
                  )
                ORDER BY s.updated_at DESC NULLS LAST, s.created_at DESC NULLS LAST
                """
            )
        )
        return {"items": _dict_rows(result)}
    finally:
        db.close()


@router.post("/api/ads/settings")
def save_ads_settings(payload: dict):
    playlist_id = str(payload.get("playlist_id") or "").strip()
    if not playlist_id:
        raise HTTPException(status_code=400, detail="playlist_id is required")

    settings = payload.get("settings") or {}
    for key in ["ads", "color", "category", "genre", "country", "master_playlist"]:
        if payload.get(key) is not None:
            settings[key] = payload.get(key)

    db: Session = SessionLocal()
    try:
        _ensure_ads_playlist_settings_table(db)

        db.execute(
            text(
                """
                INSERT INTO ads_playlist_settings (
                    playlist_id,
                    playlist_name,
                    account_name,
                    category,
                    genre,
                    country,
                    master_playlist,
                    ad_date,
                    campaign_status,
                    budget,
                    followers,
                    last_synced,
                    settings,
                    is_deleted,
                    deleted_at,
                    updated_at
                ) VALUES (
                    :playlist_id,
                    :playlist_name,
                    :account_name,
                    :category,
                    :genre,
                    :country,
                    :master_playlist,
                    :ad_date,
                    :campaign_status,
                    :budget,
                    :followers,
                    :last_synced,
                    CAST(:settings AS JSONB),
                    FALSE,
                    NULL,
                    NOW()
                )
                ON CONFLICT (playlist_id) DO UPDATE SET
                    playlist_name = COALESCE(EXCLUDED.playlist_name, ads_playlist_settings.playlist_name),
                    account_name = COALESCE(EXCLUDED.account_name, ads_playlist_settings.account_name),
                    category = EXCLUDED.category,
                    genre = EXCLUDED.genre,
                    country = EXCLUDED.country,
                    master_playlist = EXCLUDED.master_playlist,
                    ad_date = EXCLUDED.ad_date,
                    campaign_status = COALESCE(EXCLUDED.campaign_status, ads_playlist_settings.campaign_status),
                    budget = COALESCE(EXCLUDED.budget, ads_playlist_settings.budget),
                    followers = COALESCE(EXCLUDED.followers, ads_playlist_settings.followers),
                    last_synced = COALESCE(EXCLUDED.last_synced, ads_playlist_settings.last_synced),
                    settings = EXCLUDED.settings,
                    is_deleted = FALSE,
                    deleted_at = NULL,
                    updated_at = NOW()
                """
            ),
            {
                "playlist_id": playlist_id,
                "playlist_name": payload.get("playlist_name"),
                "account_name": payload.get("account_name"),
                "category": payload.get("category"),
                "genre": payload.get("genre"),
                "country": payload.get("country"),
                "master_playlist": payload.get("master_playlist"),
                "ad_date": payload.get("ad_date"),
                "campaign_status": payload.get("campaign_status"),
                "budget": payload.get("budget"),
                "followers": payload.get("followers"),
                "last_synced": payload.get("last_synced"),
                "settings": json.dumps(settings),
            },
        )
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.post("/api/ads/settings/import-csv-meta")
def import_ads_settings_meta_from_csv_seed():
    """Fast bulk import for Category, Genre, Country, and Master from the Ads CSV seed.

    This avoids the older slow row-by-row import. It:
    - loads the JSON seed
    - dedupes by Spotify playlist ID
    - bulk-matches against playlists.spotify_id / spotify_playlist_id
    - bulk-upserts ads_playlist_settings
    - bulk-updates ads_meta and playlists.genre when those tables/columns exist
    """
    seed_rows = _load_ads_csv_seed()
    rows = _normalize_seed_rows(seed_rows)

    if not rows:
        return {
            "success": True,
            "source": "ads_table_meta_seed.json",
            "total_seed_rows": len(seed_rows),
            "processed": 0,
            "settings_upserted": 0,
            "matched_playlists": 0,
            "missing_local_playlist": 0,
        }

    db: Session = SessionLocal()
    try:
        _ensure_ads_playlist_settings_table(db)

        playlist_has_spotify_id = _column_exists(db, "playlists", "spotify_id")
        playlist_has_spotify_playlist_id = _column_exists(db, "playlists", "spotify_playlist_id")
        playlist_has_genre = _column_exists(db, "playlists", "genre")

        match_conditions = []
        if playlist_has_spotify_id:
            match_conditions.append("p.spotify_id = s.spotify_playlist_id")
        if playlist_has_spotify_playlist_id:
            match_conditions.append("p.spotify_playlist_id = s.spotify_playlist_id")

        if not match_conditions:
            # Fallback: import settings by Spotify ID only.
            match_sql = "FALSE"
        else:
            match_sql = " OR ".join(match_conditions)

        db.execute(text("DROP TABLE IF EXISTS tmp_ads_csv_meta_import"))
        db.execute(
            text(
                """
                CREATE TEMP TABLE tmp_ads_csv_meta_import (
                    spotify_playlist_id TEXT PRIMARY KEY,
                    playlist_name TEXT,
                    account_name TEXT,
                    category TEXT,
                    genre TEXT,
                    country TEXT,
                    master_playlist TEXT
                ) ON COMMIT DROP
                """
            )
        )

        db.execute(
            text(
                """
                INSERT INTO tmp_ads_csv_meta_import (
                    spotify_playlist_id,
                    playlist_name,
                    account_name,
                    category,
                    genre,
                    country,
                    master_playlist
                )
                SELECT
                    spotify_playlist_id,
                    playlist_name,
                    account_name,
                    category,
                    genre,
                    country,
                    master_playlist
                FROM jsonb_to_recordset(CAST(:rows AS JSONB))
                AS x(
                    spotify_playlist_id TEXT,
                    playlist_name TEXT,
                    account_name TEXT,
                    category TEXT,
                    genre TEXT,
                    country TEXT,
                    master_playlist TEXT
                )
                """
            ),
            {"rows": json.dumps(rows)},
        )

        db.execute(text("CREATE INDEX IF NOT EXISTS idx_tmp_ads_csv_meta_import_spotify_id ON tmp_ads_csv_meta_import (spotify_playlist_id)"))

        # Resolve each CSV row to the local playlist id when it exists.
        db.execute(text("DROP TABLE IF EXISTS tmp_ads_csv_meta_resolved"))
        db.execute(
            text(
                f"""
                CREATE TEMP TABLE tmp_ads_csv_meta_resolved AS
                SELECT DISTINCT ON (s.spotify_playlist_id)
                    s.spotify_playlist_id,
                    COALESCE(CAST(p.id AS TEXT), s.spotify_playlist_id) AS settings_playlist_id,
                    p.id AS local_playlist_id,
                    COALESCE(s.playlist_name, p.name) AS playlist_name,
                    s.account_name,
                    s.category,
                    s.genre,
                    s.country,
                    s.master_playlist
                FROM tmp_ads_csv_meta_import s
                LEFT JOIN playlists p
                    ON ({match_sql})
                ORDER BY
                    s.spotify_playlist_id,
                    CASE WHEN p.id IS NULL THEN 1 ELSE 0 END,
                    CASE WHEN COALESCE(p.is_deleted, FALSE) = FALSE THEN 0 ELSE 1 END,
                    p.id ASC
                """
            )
        )

        count_row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS processed,
                    COUNT(local_playlist_id) AS matched_playlists,
                    COUNT(*) - COUNT(local_playlist_id) AS missing_local_playlist
                FROM tmp_ads_csv_meta_resolved
                """
            )
        ).first()

        upsert_result = db.execute(
            text(
                """
                INSERT INTO ads_playlist_settings (
                    playlist_id,
                    playlist_name,
                    account_name,
                    category,
                    genre,
                    country,
                    master_playlist,
                    settings,
                    is_deleted,
                    deleted_at,
                    updated_at
                )
                SELECT
                    settings_playlist_id,
                    playlist_name,
                    account_name,
                    category,
                    genre,
                    country,
                    master_playlist,
                    jsonb_strip_nulls(jsonb_build_object(
                        'category', category,
                        'genre', genre,
                        'country', country,
                        'master_playlist', master_playlist
                    )),
                    FALSE,
                    NULL,
                    NOW()
                FROM tmp_ads_csv_meta_resolved
                ON CONFLICT (playlist_id) DO UPDATE SET
                    playlist_name = COALESCE(EXCLUDED.playlist_name, ads_playlist_settings.playlist_name),
                    account_name = COALESCE(EXCLUDED.account_name, ads_playlist_settings.account_name),
                    category = EXCLUDED.category,
                    genre = EXCLUDED.genre,
                    country = EXCLUDED.country,
                    master_playlist = EXCLUDED.master_playlist,
                    settings = COALESCE(ads_playlist_settings.settings, '{}'::jsonb) || EXCLUDED.settings,
                    is_deleted = FALSE,
                    deleted_at = NULL,
                    updated_at = NOW()
                """
            )
        )

        # Also update older settings saved by Spotify ID when local id is used.
        db.execute(
            text(
                """
                UPDATE ads_playlist_settings old
                SET
                    category = r.category,
                    genre = r.genre,
                    country = r.country,
                    master_playlist = r.master_playlist,
                    settings = COALESCE(old.settings, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
                        'category', r.category,
                        'genre', r.genre,
                        'country', r.country,
                        'master_playlist', r.master_playlist
                    )),
                    is_deleted = FALSE,
                    deleted_at = NULL,
                    updated_at = NOW()
                FROM tmp_ads_csv_meta_resolved r
                WHERE old.playlist_id = r.spotify_playlist_id
                  AND old.playlist_id <> r.settings_playlist_id
                """
            )
        )

        ads_meta_updated = 0
        ads_meta_inserted = 0

        if _table_exists(db, "ads_meta"):
            ads_meta_columns = {
                col: _column_exists(db, "ads_meta", col)
                for col in ["category", "genre", "country", "master_playlist", "created_at", "updated_at"]
            }

            set_parts = []
            for column in ["category", "genre", "country", "master_playlist"]:
                if ads_meta_columns[column]:
                    set_parts.append(f"{column} = r.{column}")
            if ads_meta_columns["updated_at"]:
                set_parts.append("updated_at = NOW()")

            if set_parts:
                result = db.execute(
                    text(
                        f"""
                        UPDATE ads_meta m
                        SET {", ".join(set_parts)}
                        FROM tmp_ads_csv_meta_resolved r
                        WHERE m.playlist_id = r.local_playlist_id
                        """
                    )
                )
                ads_meta_updated = result.rowcount or 0

                insert_columns = ["playlist_id"]
                select_columns = ["r.local_playlist_id"]
                for column in ["category", "genre", "country", "master_playlist"]:
                    if ads_meta_columns[column]:
                        insert_columns.append(column)
                        select_columns.append(f"r.{column}")
                if ads_meta_columns["created_at"]:
                    insert_columns.append("created_at")
                    select_columns.append("NOW()")
                if ads_meta_columns["updated_at"]:
                    insert_columns.append("updated_at")
                    select_columns.append("NOW()")

                try:
                    result = db.execute(
                        text(
                            f"""
                            INSERT INTO ads_meta ({", ".join(insert_columns)})
                            SELECT {", ".join(select_columns)}
                            FROM tmp_ads_csv_meta_resolved r
                            LEFT JOIN ads_meta m ON m.playlist_id = r.local_playlist_id
                            WHERE r.local_playlist_id IS NOT NULL
                              AND m.playlist_id IS NULL
                            """
                        )
                    )
                    ads_meta_inserted = result.rowcount or 0
                except Exception:
                    # If ads_meta has extra required columns/constraints, the settings import still succeeds.
                    db.rollback()
                    # Recreate temp tables are gone after rollback, so fail cleanly with useful details.
                    raise HTTPException(
                        status_code=500,
                        detail="ads_playlist_settings import succeeded until ads_meta insert, but ads_meta has incompatible constraints. Use v2b without ads_meta insert or send Render logs.",
                    )

        playlists_genre_updated = 0
        if playlist_has_genre:
            result = db.execute(
                text(
                    """
                    UPDATE playlists p
                    SET genre = r.genre
                    FROM tmp_ads_csv_meta_resolved r
                    WHERE p.id = r.local_playlist_id
                      AND r.genre IS NOT NULL
                    """
                )
            )
            playlists_genre_updated = result.rowcount or 0

        filter_result = _save_filter_options_bulk(db, rows)

        db.commit()

        return {
            "success": True,
            "source": "ads_table_meta_seed.json",
            "mode": "fast-bulk-v2",
            "total_seed_rows": len(seed_rows),
            "processed": int(count_row.processed or 0),
            "matched_playlists": int(count_row.matched_playlists or 0),
            "missing_local_playlist": int(count_row.missing_local_playlist or 0),
            "settings_upserted": upsert_result.rowcount or len(rows),
            "ads_meta_updated": ads_meta_updated,
            "ads_meta_inserted": ads_meta_inserted,
            "playlists_genre_updated": playlists_genre_updated,
            "filter_options_seen": filter_result["inserted_or_existing"],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.post("/api/ads/settings/cleanup-stale")
def cleanup_stale_ads_settings():
    db: Session = SessionLocal()
    try:
        _ensure_ads_playlist_settings_table(db)

        result = db.execute(
            text(
                """
                DELETE FROM ads_playlist_settings s
                WHERE EXISTS (
                    SELECT 1
                    FROM playlists p
                    WHERE (
                        CAST(p.id AS TEXT) = CAST(s.playlist_id AS TEXT)
                        OR p.spotify_id = CAST(s.playlist_id AS TEXT)
                        OR p.spotify_playlist_id = CAST(s.playlist_id AS TEXT)
                    )
                    AND COALESCE(p.is_deleted, FALSE) = TRUE
                )
                """
            )
        )

        db.commit()
        return {"success": True, "deleted": result.rowcount or 0}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()