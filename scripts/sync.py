import os
import sys
import requests
from datetime import datetime, timezone

BACKEND_URL = os.getenv("BACKEND_URL", "https://spotify-growth-hub-backend.onrender.com")

ENDPOINTS = [
    "/api/health",
]

def main():
    print(f"Daily sync started at {datetime.now(timezone.utc).isoformat()}")
    print(f"Backend URL: {BACKEND_URL}")

    failed = False

    for endpoint in ENDPOINTS:
        url = f"{BACKEND_URL.rstrip('/')}{endpoint}"
        print(f"Calling {url}")

        try:
            response = requests.get(url, timeout=60)
            print(f"Status: {response.status_code}")
            print(response.text[:1000])

            if response.status_code >= 400:
                failed = True

        except Exception as exc:
            failed = True
            print(f"Error calling {url}: {exc}")

    if failed:
        print("Daily sync finished with errors.")
        sys.exit(1)

    print("Daily sync finished successfully.")

if __name__ == "__main__":
    main()