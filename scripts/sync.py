import os
import sys
import time
from datetime import datetime, timezone
from typing import Iterable

import requests


BACKEND_URL = os.getenv("BACKEND_URL", "https://spotify-growth-hub-backend.onrender.com").rstrip("/")

# Best option for now:
# GitHub Actions stays only as the scheduler.
# The backend owns the data/database logic.
#
# Add real backend job endpoints here when we create them, for example:
#   /api/jobs/daily-sync
#   /api/playlists/sync-followers
#   /api/artist-library/sync
#
# 404 endpoints are treated as "not implemented yet" and skipped.
DEFAULT_ENDPOINTS = [
    "/",
    "/api/accounts",
    "/api/playlists",
    "/api/scheduling",
    "/api/song-metrics",
    "/api/artist-library",
]


def parse_endpoints() -> list[str]:
    raw = os.getenv("SYNC_ENDPOINTS", "").strip()
    if not raw:
        return DEFAULT_ENDPOINTS

    return [
        endpoint.strip()
        for endpoint in raw.split(",")
        if endpoint.strip()
    ]


def call_endpoint(endpoint: str) -> tuple[int | None, str]:
    url = f"{BACKEND_URL}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    print(f"\n→ Calling {url}")

    try:
        response = requests.get(url, timeout=90)
        body_preview = response.text[:1200].replace("\n", " ")
        print(f"  Status: {response.status_code}")
        print(f"  Body: {body_preview}")
        return response.status_code, body_preview
    except Exception as exc:
        print(f"  Request failed: {exc}")
        return None, str(exc)


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    print(f"Daily Data Sync started at {started_at}")
    print(f"Backend URL: {BACKEND_URL}")

    endpoints = parse_endpoints()
    print(f"Endpoints: {', '.join(endpoints)}")

    has_success = False
    has_hard_error = False

    for index, endpoint in enumerate(endpoints):
        status, _ = call_endpoint(endpoint)

        if status is None:
            has_hard_error = True
        elif 200 <= status < 300:
            has_success = True
        elif status == 404:
            # Allows us to safely add future job endpoint names before the backend route exists.
            print("  Skipped: endpoint not implemented yet.")
        elif 400 <= status < 500:
            # Usually auth/missing route/missing query. Log it, but do not break the daily runner.
            print("  Warning: client-side/API response. Check if this endpoint needs auth or params.")
        elif status >= 500:
            has_hard_error = True
            print("  Hard error: backend server error.")

        if index < len(endpoints) - 1:
            time.sleep(2)

    finished_at = datetime.now(timezone.utc).isoformat()
    print(f"\nDaily Data Sync finished at {finished_at}")

    if has_hard_error:
        print("Result: failed because at least one endpoint had a request/server error.")
        return 1

    if not has_success:
        print("Result: failed because no endpoint returned success.")
        return 1

    print("Result: success.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
