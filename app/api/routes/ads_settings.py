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
        raise HTTPException(
            status_code=500,
            detail=f"Missing seed file: {seed_path}",
        )

    with seed_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise HTTPException(status_code=500, detail="ads_table_meta_seed.json must contain a list")

    return payload


def _find_playlist_by_spotify_id(db: Session, spotify_playlist_id: str):
    """Return the active local playlist row that matches a Spotify playlist id.

    This uses raw SQL so the import can run even if the SQLAlchemy model changed.
    """
    if not _table_exists(db, "playlists"):
        return None

    where_parts = []
    params = {"spotify_playlist_id": spotify_playlist_id}

    if _column_exists(db, "playlists", "spotify_id"):
        where_parts.append("p.spotify_id = :spotify_playlist_id")
    if _column_exists(db, "playlists", "spotify_playlist_id"):
        where_parts.append("p.spotify_playlist_id = :spotify_playlist_id")

    if not where_parts:
        return None

    account_name_select = "NULL AS account_name"
    join_accounts = ""
    if _table_exists(db, "spotify_accounts") and _column_exists(db, "playlists", "account_id"):
        account_name_columns = [
            col for col in ["display_name", "name", "email", "spotify_user_id"]
            if _column_exists(db, "spotify_accounts", col)
        ]
        if account_name_columns:
            account_name_select = "COALESCE(" + ", ".join([f"a.{col}" for col in account_name_columns]) + ") AS account_name"
            join_accounts = "LEFT JOIN spotify_accounts a ON a.id = p.account_id"

    deleted_order = ""
    if _column_exists(db, "playlists", "is_deleted"):
        deleted_order = "CASE WHEN COALESCE(p.is_deleted, FALSE) = FALSE THEN 0 ELSE 1 END,"

    name_select = "p.name" if _column_exists(db, "playlists", "name") else "NULL"
    account_id_select = "p.account_id" if _column_exists(db, "playlists", "account_id") else "NULL"

    query = text(
        f"""
        SELECT
            p.id,
            {name_select} AS name,
            {account_id_select} AS account_id,
            {account_name_select}
        FROM playlists p
        {join_accounts}
        WHERE {" OR ".join(where_parts)}
        ORDER BY {deleted_order} p.id ASC
        LIMIT 1
        """
    )

    return db.execute(query, params).first()


def _upsert_ads_settings_row(db: Session, playlist_id: str, payload: dict):
    settings_patch = {
        "category": payload.get("category"),
        "genre": payload.get("genre"),
        "country": payload.get("country"),
        "master_playlist": payload.get("master_playlist"),
    }
    settings_patch = {key: value for key, value in settings_patch.items() if value is not None}

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
                CAST(:settings_patch AS JSONB),
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
                settings = COALESCE(ads_playlist_settings.settings, '{}'::jsonb) || EXCLUDED.settings,
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
            "settings_patch": json.dumps(settings_patch),
        },
    )


def _update_ads_meta(db: Session, internal_playlist_id: int, payload: dict):
    """Update ads_meta if it exists. Insert is attempted but non-fatal.

    The Ads page also loads ads_playlist_settings, so ads_meta is a compatibility sync.
    """
    if not _table_exists(db, "ads_meta"):
        return {"updated": 0, "inserted": 0, "skipped": True}

    update_columns = []
    params = {"playlist_id": internal_playlist_id}

    for csv_key, column_name in [
        ("category", "category"),
        ("genre", "genre"),
        ("country", "country"),
        ("master_playlist", "master_playlist"),
    ]:
        if _column_exists(db, "ads_meta", column_name):
            update_columns.append(f"{column_name} = :{column_name}")
            params[column_name] = payload.get(csv_key)

    if _column_exists(db, "ads_meta", "updated_at"):
        update_columns.append("updated_at = NOW()")

    if not update_columns:
        return {"updated": 0, "inserted": 0, "skipped": True}

    result = db.execute(
        text(
            f"""
            UPDATE ads_meta
            SET {", ".join(update_columns)}
            WHERE playlist_id = :playlist_id
            """
        ),
        params,
    )

    if result.rowcount and result.rowcount > 0:
        return {"updated": result.rowcount, "inserted": 0, "skipped": False}

    # Try a safe insert for playlists that do not have ads_meta yet.
    insert_columns = ["playlist_id"]
    insert_values = [":playlist_id"]
    insert_params = {"playlist_id": internal_playlist_id}

    for csv_key, column_name in [
        ("category", "category"),
        ("genre", "genre"),
        ("country", "country"),
        ("master_playlist", "master_playlist"),
    ]:
        if _column_exists(db, "ads_meta", column_name):
            insert_columns.append(column_name)
            insert_values.append(f":{column_name}")
            insert_params[column_name] = payload.get(csv_key)

    if _column_exists(db, "ads_meta", "created_at"):
        insert_columns.append("created_at")
        insert_values.append("NOW()")
    if _column_exists(db, "ads_meta", "updated_at"):
        insert_columns.append("updated_at")
        insert_values.append("NOW()")

    try:
        db.execute(
            text(
                f"""
                INSERT INTO ads_meta ({", ".join(insert_columns)})
                VALUES ({", ".join(insert_values)})
                """
            ),
            insert_params,
        )
        return {"updated": 0, "inserted": 1, "skipped": False}
    except Exception:
        # Avoid failing the whole CSV import if ads_meta has unexpected constraints.
        db.rollback()
        return {"updated": 0, "inserted": 0, "skipped": True}


def _save_filter_option(db: Session, option_type: str, value: str | None):
    value = _clean_text(value)
    if not value or not _table_exists(db, "ads_filter_options"):
        return

    try:
        db.execute(
            text(
                """
                INSERT INTO ads_filter_options (option_type, value)
                VALUES (:option_type, :value)
                ON CONFLICT (option_type, value) DO NOTHING
                """
            ),
            {"option_type": option_type, "value": value},
        )
    except Exception:
        # Non-fatal; old DBs may not have a unique index here.
        pass


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
    if payload.get("ads") is not None:
        settings["ads"] = payload.get("ads")
    if payload.get("color") is not None:
        settings["color"] = payload.get("color")
    if payload.get("category") is not None:
        settings["category"] = payload.get("category")
    if payload.get("genre") is not None:
        settings["genre"] = payload.get("genre")
    if payload.get("country") is not None:
        settings["country"] = payload.get("country")
    if payload.get("master_playlist") is not None:
        settings["master_playlist"] = payload.get("master_playlist")

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
                    playlist_name = EXCLUDED.playlist_name,
                    account_name = EXCLUDED.account_name,
                    category = EXCLUDED.category,
                    genre = EXCLUDED.genre,
                    country = EXCLUDED.country,
                    master_playlist = EXCLUDED.master_playlist,
                    ad_date = EXCLUDED.ad_date,
                    campaign_status = EXCLUDED.campaign_status,
                    budget = EXCLUDED.budget,
                    followers = EXCLUDED.followers,
                    last_synced = EXCLUDED.last_synced,
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
    """Import Category, Genre, Country, and Master from the uploaded Ads CSV seed.

    Matches rows by CSV Spotify playlist ID against playlists.spotify_id / spotify_playlist_id.
    If a playlist exists locally, the ads_playlist_settings row is stored under the local playlist id,
    which matches what the Ads page autosave uses.
    """
    seed_rows = _load_ads_csv_seed()

    db: Session = SessionLocal()
    try:
        _ensure_ads_playlist_settings_table(db)

        total = 0
        matched_playlists = 0
        settings_upserted = 0
        ads_meta_updated = 0
        ads_meta_inserted = 0
        missing_local_playlist = 0
        skipped_no_id = 0

        for seed in seed_rows:
            spotify_playlist_id = _clean_text(seed.get("spotify_playlist_id"))
            if not spotify_playlist_id:
                skipped_no_id += 1
                continue

            total += 1

            local_playlist = _find_playlist_by_spotify_id(db, spotify_playlist_id)
            local_playlist_id = None
            playlist_id_for_settings = spotify_playlist_id

            payload = {
                "playlist_name": _clean_text(seed.get("playlist_name")),
                "account_name": _clean_text(seed.get("account_name")),
                "category": _clean_text(seed.get("category")),
                "genre": _clean_text(seed.get("genre")),
                "country": _clean_text(seed.get("country")),
                "master_playlist": _clean_text(seed.get("master_playlist")),
            }

            if local_playlist:
                matched_playlists += 1
                local_playlist_id = int(local_playlist.id)
                playlist_id_for_settings = str(local_playlist_id)
                payload["playlist_name"] = payload["playlist_name"] or _clean_text(local_playlist.name)
                payload["account_name"] = payload["account_name"] or _clean_text(local_playlist.account_name)
            else:
                missing_local_playlist += 1

            _upsert_ads_settings_row(db, playlist_id_for_settings, payload)
            settings_upserted += 1

            # Also update legacy rows that might have been saved by Spotify ID before.
            if playlist_id_for_settings != spotify_playlist_id:
                db.execute(
                    text(
                        """
                        UPDATE ads_playlist_settings
                        SET
                            category = :category,
                            genre = :genre,
                            country = :country,
                            master_playlist = :master_playlist,
                            settings = COALESCE(settings, '{}'::jsonb) || CAST(:settings_patch AS JSONB),
                            is_deleted = FALSE,
                            deleted_at = NULL,
                            updated_at = NOW()
                        WHERE playlist_id = :spotify_playlist_id
                        """
                    ),
                    {
                        "spotify_playlist_id": spotify_playlist_id,
                        "category": payload.get("category"),
                        "genre": payload.get("genre"),
                        "country": payload.get("country"),
                        "master_playlist": payload.get("master_playlist"),
                        "settings_patch": json.dumps({
                            key: value for key, value in {
                                "category": payload.get("category"),
                                "genre": payload.get("genre"),
                                "country": payload.get("country"),
                                "master_playlist": payload.get("master_playlist"),
                            }.items() if value is not None
                        }),
                    },
                )

            if local_playlist_id is not None:
                meta_result = _update_ads_meta(db, local_playlist_id, payload)
                ads_meta_updated += meta_result.get("updated", 0) or 0
                ads_meta_inserted += meta_result.get("inserted", 0) or 0

                if payload.get("genre") and _column_exists(db, "playlists", "genre"):
                    db.execute(
                        text("UPDATE playlists SET genre = :genre WHERE id = :playlist_id"),
                        {"genre": payload.get("genre"), "playlist_id": local_playlist_id},
                    )

            _save_filter_option(db, "category", payload.get("category"))
            _save_filter_option(db, "genre", payload.get("genre"))

        db.commit()
        return {
            "success": True,
            "source": "ads_table_meta_seed.json",
            "total_seed_rows": len(seed_rows),
            "processed": total,
            "matched_playlists": matched_playlists,
            "missing_local_playlist": missing_local_playlist,
            "settings_upserted": settings_upserted,
            "ads_meta_updated": ads_meta_updated,
            "ads_meta_inserted": ads_meta_inserted,
            "skipped_no_id": skipped_no_id,
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
    """Hide/delete ads settings that belong to playlists marked deleted/missing.

    This keeps Ads from showing settings for Spotify playlists that are no longer active.
    """
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
        return {
            "success": True,
            "deleted": result.rowcount or 0,
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()
