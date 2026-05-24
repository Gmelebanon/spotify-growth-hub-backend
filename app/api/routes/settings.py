import hashlib
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from supabase import Client, create_client

router = APIRouter(prefix="/api/settings", tags=["settings"])


VALID_ROLES = {"admin", "viewer"}
MASTER_USERNAME = "gmelebanon"


def get_supabase() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_URL")

    if not supabase_key:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_SERVICE_ROLE_KEY")

    return create_client(supabase_url, supabase_key)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_master_username(username: Any) -> bool:
    return str(username or "").strip().lower() == MASTER_USERNAME


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"sha256${salt}${digest}"


def safe_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.fromisoformat(f"{text}T00:00:00+00:00")
        except Exception:
            return None


def freshness_label(value: Any) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return "Not synced yet"

    now = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    delta = now - parsed
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


def get_latest_table_push(supabase: Client, table_name: str) -> Optional[Dict[str, Any]]:
    # Most project tables use created_at. follower_history also has date.
    try:
        response = (
            supabase.table(table_name)
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception:
        return None




def get_latest_follower_history_sync(supabase: Client) -> Optional[Dict[str, Any]]:
    # Settings Last Sync must come from follower_history date first, because imported
    # history rows can have a fresh data date even when created_at differs.
    try:
        response = (
            supabase.table("follower_history")
            .select("date,created_at")
            .order("date", desc=True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception:
        return None

def normalize_account(row: Dict[str, Any], global_last_sync: Optional[str] = None) -> Dict[str, Any]:
    name = (
        row.get("display_name")
        or row.get("name")
        or row.get("username")
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
        "id": row.get("id"),
        "name": name,
        "lastSynced": safe_iso(last_synced),
        "status": status,
        "freshness": freshness_label(last_synced),
    }


def get_accounts_summary(supabase: Client, global_last_sync: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        response = (
            supabase.table("spotify_accounts")
            .select("*")
            .order("id", desc=False)
            .execute()
        )
        rows = response.data or []
        return [normalize_account(row, global_last_sync=global_last_sync) for row in rows]
    except Exception:
        return []


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
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "displayName": row.get("display_name"),
        "role": row.get("role") or "viewer",
        "isActive": row.get("is_active", True),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@router.get("/summary")
def get_settings_summary() -> Dict[str, Any]:
    try:
        supabase = get_supabase()

        latest_follower_history = get_latest_follower_history_sync(supabase)

        latest_data_push = None
        latest_data_push_source = None

        if latest_follower_history:
            latest_data_push = (
                latest_follower_history.get("date")
                or latest_follower_history.get("created_at")
            )
            latest_data_push_source = "follower_history"

        accounts = get_accounts_summary(supabase, global_last_sync=safe_iso(latest_data_push))

        connected_accounts = len([item for item in accounts if item.get("status") == "Connected"])
        expired_accounts = max(0, len(accounts) - connected_accounts)
        warnings = expired_accounts

        return {
            "success": True,
            "platformHealth": 100 if warnings == 0 else max(0, 100 - warnings * 10),
            "warnings": warnings,
            "connectedAccounts": connected_accounts,
            "expiredAccounts": expired_accounts,
            "syncSuccessRate": 100 if connected_accounts > 0 and warnings == 0 else 0,
            "lastDataPush": safe_iso(latest_data_push),
            "lastDataPushFreshness": freshness_label(latest_data_push),
            "lastDataPushSource": latest_data_push_source,
            "lastSync": safe_iso(latest_data_push),
            "lastSyncFreshness": freshness_label(latest_data_push),
            "lastSyncSource": latest_data_push_source,
            "accounts": accounts,
            "updatedAt": utc_now_iso(),
        }

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Settings summary error: {str(error)}")


@router.get("/users")
def list_users() -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        response = (
            supabase.table("app_users")
            .select("id,username,display_name,role,is_active,created_at,updated_at")
            .order("created_at", desc=False)
            .execute()
        )
        return {"success": True, "users": [serialize_user(row) for row in response.data or []]}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not load users: {str(error)}")


@router.post("/users")
def create_user(payload: CreateUserPayload) -> Dict[str, Any]:
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
        supabase = get_supabase()
        now = utc_now_iso()
        row = {
            "username": username,
            "display_name": payload.display_name or username,
            "password_hash": hash_password(payload.password),
            "role": role,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        response = supabase.table("app_users").insert(row).execute()
        created = response.data[0] if response.data else row
        return {"success": True, "user": serialize_user(created)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not create user: {str(error)}")


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UpdateUserPayload) -> Dict[str, Any]:
    update_payload: Dict[str, Any] = {"updated_at": utc_now_iso()}

    if payload.username is not None:
        username = payload.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="Username cannot be empty")
        update_payload["username"] = username

    if payload.display_name is not None:
        update_payload["display_name"] = payload.display_name.strip() or None

    if payload.role is not None:
        role = payload.role.strip().lower()
        if role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Role must be admin or viewer")
        update_payload["role"] = role

    if payload.is_active is not None:
        update_payload["is_active"] = payload.is_active

    if payload.password:
        if len(payload.password) < 4:
            raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
        update_payload["password_hash"] = hash_password(payload.password)

    try:
        supabase = get_supabase()
        existing_response = (
            supabase.table("app_users")
            .select("id,username,role,is_active")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        existing_rows = existing_response.data or []
        existing_user = existing_rows[0] if existing_rows else {}

        if is_master_username(existing_user.get("username")):
            # Master account is always admin and active. Password can still be changed.
            update_payload.pop("role", None)
            update_payload.pop("is_active", None)

        response = (
            supabase.table("app_users")
            .update(update_payload)
            .eq("id", user_id)
            .execute()
        )
        updated = response.data[0] if response.data else {"id": user_id, **update_payload}
        return {"success": True, "user": serialize_user(updated)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not update user: {str(error)}")


@router.delete("/users/{user_id}")
def delete_user(user_id: str) -> Dict[str, Any]:
    try:
        supabase = get_supabase()
        existing_response = (
            supabase.table("app_users")
            .select("id,username")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        existing_rows = existing_response.data or []
        if existing_rows and is_master_username(existing_rows[0].get("username")):
            raise HTTPException(status_code=400, detail="Master account cannot be deleted")

        response = (
            supabase.table("app_users")
            .delete()
            .eq("id", user_id)
            .execute()
        )
        return {"success": True, "deletedUserId": user_id, "result": response.data or []}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not delete user: {str(error)}")
