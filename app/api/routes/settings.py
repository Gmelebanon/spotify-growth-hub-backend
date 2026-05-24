import hashlib
import secrets
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])

VALID_ROLES = {"admin", "viewer"}
MASTER_USERNAME = "gmelebanon"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def is_master_username(username: Any) -> bool:
    return str(username or "").strip().lower() == MASTER_USERNAME


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"sha256${salt}${digest}"


def to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


def safe_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        text_value = str(value)
        try:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        except Exception:
            try:
                parsed = datetime.fromisoformat(f"{text_value[:10]}T00:00:00+00:00")
            except Exception:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def freshness_label(value: Any) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return "Not synced yet"

    delta = utc_now() - parsed
    total_seconds = max(0, int(delta.total_seconds()))

    if total_seconds < 60:
        return "just now"

    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"

    days = hours // 24
    return f"{days}d ago"


def table_exists(db: Session, table_name: str) -> bool:
    result = db.execute(
        text(
            """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = 'public'
                AND table_name = :table_name
            ) AS exists
            """
        ),
        {"table_name": table_name},
    ).scalar()
    return bool(result)


def count_table(db: Session, table_name: str) -> int:
    if not table_exists(db, table_name):
        return 0
    return int(db.execute(text(f'SELECT COUNT(*) FROM public."{table_name}"')).scalar() or 0)


def get_latest_follower_history_sync(db: Session) -> Optional[Dict[str, Any]]:
    if not table_exists(db, "follower_history"):
        return None

    row = db.execute(
        text(
            """
            SELECT date, created_at
            FROM public.follower_history
            WHERE date IS NOT NULL OR created_at IS NOT NULL
            ORDER BY date DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 1
            """
        )
    ).first()
    return to_dict(row) if row else None


def normalize_account(row: Dict[str, Any], global_last_sync: Optional[Any] = None) -> Dict[str, Any]:
    name = (
        row.get("display_name")
        or row.get("name")
        or row.get("username")
        or row.get("account")
        or row.get("account_name")
        or f"Account {row.get('id', '')}"
    )
    last_synced = (
        global_last_sync
        or row.get("last_synced_at")
        or row.get("last_synced")
        or row.get("updated_at")
        or row.get("created_at")
    )
    status = "Connected" if row.get("is_active", True) is not False else "Disconnected"
    return {
        "id": safe_value(row.get("id") or name),
        "name": str(name),
        "lastSynced": safe_value(last_synced),
        "status": status,
        "freshness": freshness_label(last_synced),
    }


def get_accounts_from_spotify_accounts(db: Session, global_last_sync: Optional[Any]) -> List[Dict[str, Any]]:
    if not table_exists(db, "spotify_accounts"):
        return []

    rows = db.execute(text("SELECT * FROM public.spotify_accounts ORDER BY id ASC")).fetchall()
    return [normalize_account(to_dict(row), global_last_sync=global_last_sync) for row in rows]


def get_accounts_from_playlists(db: Session, global_last_sync: Optional[Any]) -> List[Dict[str, Any]]:
    if not table_exists(db, "playlists"):
        return []

    columns = [
        str(row[0])
        for row in db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'playlists'
                ORDER BY ordinal_position
                """
            )
        ).fetchall()
    ]

    preferred_columns = ["account", "account_name", "spotify_account", "owner", "username"]
    account_column = next((col for col in preferred_columns if col in columns), None)
    if not account_column:
        return []

    rows = db.execute(
        text(
            f"""
            SELECT DISTINCT {account_column} AS name
            FROM public.playlists
            WHERE {account_column} IS NOT NULL
              AND TRIM(CAST({account_column} AS TEXT)) <> ''
            ORDER BY {account_column} ASC
            """
        )
    ).fetchall()

    return [
        normalize_account({"id": index + 1, "name": to_dict(row).get("name"), "is_active": True}, global_last_sync)
        for index, row in enumerate(rows)
    ]


def get_accounts_summary(db: Session, global_last_sync: Optional[Any]) -> List[Dict[str, Any]]:
    accounts = get_accounts_from_spotify_accounts(db, global_last_sync)
    if accounts:
        return accounts
    return get_accounts_from_playlists(db, global_last_sync)


class CreateUserPayload(BaseModel):
    username: str
    password: str
    role: str = Field(default="viewer")
    display_name: Optional[str] = Field(default=None, alias="displayName")

    class Config:
        populate_by_name = True


class UpdateUserPayload(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = Field(default=None, alias="isActive")
    display_name: Optional[str] = Field(default=None, alias="displayName")

    class Config:
        populate_by_name = True


def serialize_user(row: Dict[str, Any]) -> Dict[str, Any]:
    username = row.get("username")
    role = row.get("role") or "viewer"
    is_active = row.get("is_active")

    if is_master_username(username):
        role = "admin"
        is_active = True

    return {
        "id": safe_value(row.get("id")),
        "username": username,
        "displayName": row.get("display_name") or username,
        "role": role,
        "isActive": True if is_active is None else bool(is_active),
        "createdAt": safe_value(row.get("created_at")),
        "updatedAt": safe_value(row.get("updated_at")),
    }


def ensure_app_users_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE EXTENSION IF NOT EXISTS pgcrypto;

            CREATE TABLE IF NOT EXISTS public.app_users (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              username text NOT NULL UNIQUE,
              display_name text,
              password_hash text NOT NULL,
              role text NOT NULL DEFAULT 'viewer',
              is_active boolean NOT NULL DEFAULT true,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );
            """
        )
    )
    db.commit()


@router.get("/summary")
def get_settings_summary(db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        latest_follower_history = get_latest_follower_history_sync(db)
        latest_sync = None
        latest_sync_source = None

        if latest_follower_history:
            latest_sync = latest_follower_history.get("date") or latest_follower_history.get("created_at")
            latest_sync_source = "follower_history"

        accounts = get_accounts_summary(db, global_last_sync=latest_sync)
        connected_accounts = len([item for item in accounts if item.get("status") == "Connected"])
        expired_accounts = max(0, len(accounts) - connected_accounts)
        warnings = expired_accounts

        return {
            "success": True,
            "source": "DATABASE_URL",
            "platformHealth": 100 if warnings == 0 else max(0, 100 - warnings * 10),
            "warnings": warnings,
            "connectedAccounts": connected_accounts,
            "expiredAccounts": expired_accounts,
            "syncSuccessRate": 100 if connected_accounts > 0 and warnings == 0 else 0,
            "lastDataPush": safe_value(latest_sync),
            "lastDataPushFreshness": freshness_label(latest_sync),
            "lastDataPushSource": latest_sync_source,
            "lastSync": safe_value(latest_sync),
            "lastSyncFreshness": freshness_label(latest_sync),
            "lastSyncSource": latest_sync_source,
            "accounts": accounts,
            "updatedAt": utc_now_iso(),
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Settings summary error: {str(error)}")


@router.get("/users")
def list_users(db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        ensure_app_users_table(db)
        rows = db.execute(
            text(
                """
                SELECT id, username, display_name, role, is_active, created_at, updated_at
                FROM public.app_users
                ORDER BY created_at ASC
                """
            )
        ).fetchall()
        return {
            "success": True,
            "source": "DATABASE_URL app_users",
            "users": [serialize_user(to_dict(row)) for row in rows],
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not load users: {str(error)}")


@router.post("/users")
def create_user(payload: CreateUserPayload, db: Session = Depends(get_db)) -> Dict[str, Any]:
    username = payload.username.strip()
    role = payload.role.strip().lower()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not payload.password or len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Role must be admin or viewer")
    if is_master_username(username):
        role = "admin"

    try:
        ensure_app_users_table(db)
        now = utc_now()
        row = db.execute(
            text(
                """
                INSERT INTO public.app_users (username, display_name, password_hash, role, is_active, created_at, updated_at)
                VALUES (:username, :display_name, :password_hash, :role, true, :created_at, :updated_at)
                ON CONFLICT (username) DO UPDATE SET
                  display_name = EXCLUDED.display_name,
                  password_hash = EXCLUDED.password_hash,
                  role = EXCLUDED.role,
                  is_active = true,
                  updated_at = EXCLUDED.updated_at
                RETURNING id, username, display_name, role, is_active, created_at, updated_at
                """
            ),
            {
                "username": username,
                "display_name": payload.display_name or username,
                "password_hash": hash_password(payload.password),
                "role": role,
                "created_at": now,
                "updated_at": now,
            },
        ).first()
        db.commit()
        return {"success": True, "source": "DATABASE_URL app_users", "user": serialize_user(to_dict(row))}
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not create user: {str(error)}")


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UpdateUserPayload, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        ensure_app_users_table(db)
        existing_row = db.execute(
            text(
                """
                SELECT id, username, role, is_active
                FROM public.app_users
                WHERE id::text = :user_id
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).first()
        if not existing_row:
            raise HTTPException(status_code=404, detail="User not found")

        existing_user = to_dict(existing_row)
        update_fields: Dict[str, Any] = {"updated_at": utc_now()}

        if payload.username is not None:
            username = payload.username.strip()
            if not username:
                raise HTTPException(status_code=400, detail="Username cannot be empty")
            update_fields["username"] = username

        if payload.display_name is not None:
            update_fields["display_name"] = payload.display_name.strip() or None

        if payload.role is not None:
            role = payload.role.strip().lower()
            if role not in VALID_ROLES:
                raise HTTPException(status_code=400, detail="Role must be admin or viewer")
            update_fields["role"] = role

        if payload.is_active is not None:
            update_fields["is_active"] = payload.is_active

        if payload.password:
            if len(payload.password) < 4:
                raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
            update_fields["password_hash"] = hash_password(payload.password)

        if is_master_username(existing_user.get("username")):
            update_fields.pop("role", None)
            update_fields.pop("is_active", None)
            update_fields["role"] = "admin"
            update_fields["is_active"] = True

        assignments = ", ".join([f"{key} = :{key}" for key in update_fields.keys()])
        params = {**update_fields, "user_id": user_id}
        updated_row = db.execute(
            text(
                f"""
                UPDATE public.app_users
                SET {assignments}
                WHERE id::text = :user_id
                RETURNING id, username, display_name, role, is_active, created_at, updated_at
                """
            ),
            params,
        ).first()
        db.commit()
        return {"success": True, "source": "DATABASE_URL app_users", "user": serialize_user(to_dict(updated_row))}
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not update user: {str(error)}")


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        ensure_app_users_table(db)
        existing_row = db.execute(
            text(
                """
                SELECT id, username
                FROM public.app_users
                WHERE id::text = :user_id
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).first()
        if not existing_row:
            raise HTTPException(status_code=404, detail="User not found")

        existing_user = to_dict(existing_row)
        if is_master_username(existing_user.get("username")):
            raise HTTPException(status_code=400, detail="Master account cannot be deleted")

        db.execute(text("DELETE FROM public.app_users WHERE id::text = :user_id"), {"user_id": user_id})
        db.commit()
        return {"success": True, "source": "DATABASE_URL app_users", "deletedUserId": user_id}
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not delete user: {str(error)}")


@router.get("/debug")
def settings_debug(db: Session = Depends(get_db)) -> Dict[str, Any]:
    database_url = getattr(app_settings, "DATABASE_URL", "") or ""
    parsed = urlparse(database_url)

    latest_follower_history = get_latest_follower_history_sync(db)

    debug_tables = {}
    for table_name in ["app_users", "follower_history", "spotify_accounts", "playlists"]:
        try:
            debug_tables[table_name] = {
                "exists": table_exists(db, table_name),
                "count": count_table(db, table_name),
            }
        except Exception as error:
            debug_tables[table_name] = {
                "exists": False,
                "count": 0,
                "error": str(error),
            }

    return {
        "success": True,
        "source": "DATABASE_URL",
        "databaseHost": parsed.hostname,
        "databaseName": parsed.path.lstrip("/") if parsed.path else None,
        "updatedAt": utc_now_iso(),
        "tables": debug_tables,
        "latestFollowerHistory": latest_follower_history,
    }
