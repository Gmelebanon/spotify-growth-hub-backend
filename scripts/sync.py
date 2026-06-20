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

ENDPOINTS = [
    "/",
    "/api/accounts",
    "/api/playlists",
    "/api/scheduling",
    "/api/song-metrics",
    "/api/artist-library",
    "/api/trends/tiktok/sync?country=all",
]

TIMEOUT_SECONDS = int(os.getenv("SYNC_TIMEOUT_SECONDS", "60"))


def absolute_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path

    if not BACKEND_BASE_URL.startswith(("http://", "https://")):
        raise ValueError(
            "BACKEND_BASE_URL must include https:// or http://. "
            f"Current value: {BACKEND_BASE_URL!r}"
        )

    return f"{BACKEND_BASE_URL}/{path.lstrip('/')}"


def call_endpoint(path: str) -> bool:
    url = absolute_url(path)
    print(f"\n→ Calling {url}")

    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        print(f"  Status: {response.status_code}")

        if response.status_code >= 500:
            print(f"  Server error: {response.text[:500]}")
            return False

        if response.status_code >= 400:
            print(f"  Warning: endpoint returned {response.status_code}: {response.text[:500]}")
            return True

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
            print(f"  Warning: could not mark sync status: {response.text[:500]}")
    except requests.RequestException as exc:
        print(f"\n→ Warning: could not mark sync status: {exc}")


def main() -> int:
    print(f"Daily Data Sync started at {datetime.now(timezone.utc).isoformat()}")
    print(f"Backend base URL: {BACKEND_BASE_URL}")

    success = True

    for endpoint in ENDPOINTS:
        if not call_endpoint(endpoint):
            success = False

    mark_sync_status(success)

    print(f"\nDaily Data Sync finished at {datetime.now(timezone.utc).isoformat()}")

    if success:
        print("Result: success")
        return 0

    print("Result: failed because at least one endpoint had a request/server error.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
