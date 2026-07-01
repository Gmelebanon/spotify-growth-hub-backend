import os
import sys
import time
from datetime import datetime, timezone

import requests


BACKEND_URL = os.getenv("BACKEND_URL", "https://spotify-growth-hub-backend.onrender.com").rstrip("/")

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

    return [endpoint.strip() for endpoint in raw.split(",") if endpoint.strip()]


def build_url(endpoint: str) -> str:
    return f"{BACKEND_URL}{endpoint if endpoint.startswith('/') else '/' + endpoint}"


def call_endpoint(endpoint: str) -> tuple[int | None, str]:
    url = build_url(endpoint)
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


def mark_sync_status(status: str, message: str) -> None:
    url = build_url("/api/sync-status/mark")
    print(f"\n→ Marking sync status: {status}")

    try:
        response = requests.post(
            url,
            json={
                "status": status,
                "source": "github_actions",
                "message": message[:500],
            },
            timeout=60,
        )
        print(f"  Status marker response: {response.status_code}")
        print(f"  Body: {response.text[:1000]}")
    except Exception as exc:
        print(f"  Could not mark sync status: {exc}")


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    print(f"Daily Data Sync started at {started_at}")
    print(f"Backend URL: {BACKEND_URL}")

    endpoints = parse_endpoints()
    print(f"Endpoints: {', '.join(endpoints)}")

    has_success = False
    has_hard_error = False
    warnings: list[str] = []

    for index, endpoint in enumerate(endpoints):
        status, detail = call_endpoint(endpoint)

        if status is None:
            has_hard_error = True
            warnings.append(f"{endpoint}: request failed - {detail}")
        elif 200 <= status < 300:
            has_success = True
        elif status == 404:
            print("  Skipped: endpoint not implemented yet.")
            warnings.append(f"{endpoint}: 404 skipped")
        elif 400 <= status < 500:
            print("  Warning: client-side/API response. Check if this endpoint needs auth or params.")
            warnings.append(f"{endpoint}: HTTP {status}")
        elif status >= 500:
            has_hard_error = True
            print("  Hard error: backend server error.")
            warnings.append(f"{endpoint}: HTTP {status}")

        if index < len(endpoints) - 1:
            time.sleep(2)

    finished_at = datetime.now(timezone.utc).isoformat()
    print(f"\nDaily Data Sync finished at {finished_at}")

    if has_hard_error:
        message = "Daily sync finished with errors. " + "; ".join(warnings)
        mark_sync_status("failed", message)
        print(f"Result: {message}")
        return 1

    if not has_success:
        message = "Daily sync finished but no endpoint returned success. " + "; ".join(warnings)
        mark_sync_status("failed", message)
        print(f"Result: {message}")
        return 1

    message = "Daily sync completed successfully. " + ("Warnings: " + "; ".join(warnings) if warnings else "")
    mark_sync_status("success", message)
    print("Result: success.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
