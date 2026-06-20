import os
import sys
from datetime import datetime, timezone

import requests


DEFAULT_BACKEND_BASE_URL = "https://spotify-growth-hub-backend.onrender.com"

BACKEND_BASE_URL = (
    os.getenv("BACKEND_BASE_URL")
    or os.getenv("API_BASE_URL")
    or os.getenv("NEXT_PUBLIC_API_BASE_URL")
    or DEFAULT_BACKEND_BASE_URL
).strip().rstrip("/")

TIMEOUT_SECONDS = int(os.getenv("SYNC_TIMEOUT_SECONDS", "90"))

# These endpoints are for warming/checking normal app pages.
CHECK_ENDPOINTS = [
    "/",
    "/api/accounts",
    "/api/playlists",
    "/api/scheduling",
    "/api/song-metrics",
    "/api/artist-library",
]

# These endpoints are the actual refresh triggers.
# They force the backend to fetch fresh remote data where supported.
REFRESH_ENDPOINTS = [
    "/api/playlists?refresh=true",
    "/api/song-metrics?refresh=true",
    "/api/artist-library?refresh=true",
    "/api/trends/chart?platform=spotify&view=weekly_country&country=global&limit=100&refresh=true",
    "/api/trends/chart?platform=youtube&view=global_trending_weekly&country=global&limit=100&refresh=true",
    "/api/trends/tiktok/sync?country=all&force=true",
]


def absolute_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path

    if not BACKEND_BASE_URL.startswith(("http://", "https://")):
        raise ValueError(
            "BACKEND_BASE_URL must include https:// or http://. "
            f"Current value: {BACKEND_BASE_URL!r}"
        )

    return f"{BACKEND_BASE_URL}/{path.lstrip('/')}"


def call_endpoint(path: str, required: bool = True) -> bool:
    url = absolute_url(path)
    print(f"\n→ Calling {url}")

    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        print(f"  Status: {response.status_code}")

        if response.status_code >= 500:
            print(f"  Server error: {response.text[:800]}")
            return False

        if response.status_code >= 400:
            message = f"  Warning: endpoint returned {response.status_code}: {response.text[:800]}"
            print(message)
            return not required

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            if "rows" in payload and isinstance(payload["rows"], list):
                print(f"  Rows: {len(payload['rows'])}")
            if "synced" in payload and isinstance(payload["synced"], list):
                print(f"  Synced items: {len(payload['synced'])}")
                print(f"  Request count: {payload.get('request_count', 'n/a')}")
            if "last_sync" in payload:
                print(f"  Last sync: {payload.get('last_sync')}")

        return True

    except requests.RequestException as exc:
        print(f"  Request failed: {exc}")
        return False


def mark_sync_status(success: bool) -> None:
    url = absolute_url("/api/sync-status/mark")
    payload = {
        "status": "success" if success else "failed",
        "source": "github-actions",
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        print(f"\n→ Mark sync status: {response.status_code}")
        if response.status_code >= 400:
            print(f"  Warning: could not mark sync status: {response.text[:800]}")
    except requests.RequestException as exc:
        print(f"\n→ Warning: could not mark sync status: {exc}")


def main() -> int:
    print(f"Daily Data Sync started at {datetime.now(timezone.utc).isoformat()}")
    print(f"Backend base URL: {BACKEND_BASE_URL}")

    success = True

    print("\n=== Refresh endpoints ===")
    for endpoint in REFRESH_ENDPOINTS:
        if not call_endpoint(endpoint, required=True):
            success = False

    print("\n=== Check endpoints ===")
    for endpoint in CHECK_ENDPOINTS:
        if not call_endpoint(endpoint, required=False):
            success = False

    mark_sync_status(success)

    print(f"\nDaily Data Sync finished at {datetime.now(timezone.utc).isoformat()}")

    if success:
        print("Result: success")
        return 0

    print("Result: failed because at least one required refresh endpoint had a request/server error.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
