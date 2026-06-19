import os
import sys
import requests
from datetime import datetime, timezone

BACKEND_URL = os.getenv("BACKEND_URL", "https://spotify-growth-hub-backend.onrender.com")

ENDPOINTS = [
    "/",
    "/api/artists",
    "/api/songs",
]

def main():
    print(f"Daily sync started at {datetime.now(timezone.utc).isoformat()}")
    print(f"Backend URL: {BACKEND_URL}")

    had_error = False

    for endpoint in ENDPOINTS:
        url = f"{BACKEND_URL.rstrip('/')}{endpoint}"
        print(f"\nCalling {url}")

        try:
            response = requests.get(url, timeout=60)
            print(f"Status: {response.status_code}")
            print(response.text[:1000])

            if response.status_code >= 500:
                had_error = True
                print(f"Server error from {url}")

        except Exception as exc:
            had_error = True
            print(f"Request failed for {url}: {exc}")

    if had_error:
        print("Daily sync finished with server/request errors.")
        sys.exit(1)

    print("Daily sync finished successfully.")

if __name__ == "__main__":
    main()