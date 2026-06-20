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

# These calls are best-effort. If one endpoint does not support refresh=true,
# we log it but do not fail the whole scheduled workflow.
REFRESH_ENDPOINTS = [
    "/api/playlists?refresh=true",
    "/api/song-metrics?refresh=true",
    "/api/artist-library?refresh=true",
    "/api/trends/chart?platform=spotify&view=weekly_country&country=global&limit=100&refresh=true",
    "/api/trends/chart?platform=youtube&view=global_trending_weekly&country=global&limit=100&refresh=true",
    "/api/trends/chart?platform=aggregate&view=all&country=global&limit=20&refresh=true",
    "/api/trends/tiktok/sync?country=all&force=true",
]

CHECK_ENDPOINTS = [
    "/",
    "/api/accounts",
    "/api/playlists",
    "/api/scheduling",
    "/api/song-metrics",
    "/api/artist-library",
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


def call_endpoint(path: str) -> dict:
    url = absolute_url(path)
    print(f"\n→ Calling {url}")

    result = {
        "path": path,
        "url": url,
        "ok": False,
        "status": None,
        "error": "",
    }

    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        result["status"] = response.status_code
        print(f"  Status: {response.status_code}")

        if response.status_code >= 500:
            result["error"] = response.text[:800]
            print(f"  Server error: {result['error']}")
            return result

        if response.status_code >= 400:
            result["error"] = response.text[:800]
            print(f"  Warning: endpoint returned {response.status_code}: {result['error']}")
            # 4xx means this endpoint may not support refresh/query params.
            # It is not a workflow failure.
            result["ok"] = True
            return result

        result["ok"] = True

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
            if "skipped_count" in payload:
                print(f"  Skipped count: {payload.get('skipped_count')}")
            if "last_sync" in payload:
                print(f"  Last sync: {payload.get('last_sync')}")

        return result

    except requests.RequestException as exc:
        result["error"] = str(exc)
        print(f"  Request failed: {exc}")
        return result


def mark_sync_status(status: str, details: dict | None = None) -> None:
    url = absolute_url("/api/sync-status/mark")
    payload = {
        "status": status,
        "source": "github-actions",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
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

    results: list[dict] = []

    print("\n=== Refresh endpoints ===")
    for endpoint in REFRESH_ENDPOINTS:
        results.append(call_endpoint(endpoint))

    print("\n=== Check endpoints ===")
    for endpoint in CHECK_ENDPOINTS:
        results.append(call_endpoint(endpoint))

    hard_failures = [
        item for item in results
        if item.get("status") is None or (isinstance(item.get("status"), int) and item["status"] >= 500)
    ]

    # Keep GitHub Actions green unless the backend is unreachable or has 5xx errors.
    status = "success" if not hard_failures else "warning"
    mark_sync_status(status, {"failures": hard_failures[:10]})

    print(f"\nDaily Data Sync finished at {datetime.now(timezone.utc).isoformat()}")

    if hard_failures:
        print("Result: completed with warnings. Backend had request/server errors, but workflow will not fail.")
        for failure in hard_failures:
            print(f"  - {failure.get('url')}: {failure.get('status')} {failure.get('error')}")
    else:
        print("Result: success")

    return 0


if __name__ == "__main__":
    sys.exit(main())
