"""One-time importer for bulk_playlist_results CSV into Playlist Manager.

Usage from project root:
    python import_bulk_playlist_results_direct.py "C:\\Nerd Engine\\spotify-growth-hub\\bulk_playlist_results (1).csv"

This script writes directly to the app database through SQLAlchemy models.
It does NOT call the frontend or any API route.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def extract_spotify_playlist_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    patterns = [
        r"open\.spotify\.com/playlist/([A-Za-z0-9]+)",
        r"spotify:playlist:([A-Za-z0-9]+)",
        r"playlist/([A-Za-z0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    if re.fullmatch(r"[A-Za-z0-9]{16,}", text):
        return text

    return None


def safe_set(model, field: str, value) -> None:
    if hasattr(model, field):
        setattr(model, field, value)


def first_value(row: dict[str, str], *keys: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python import_bulk_playlist_results_direct.py "C:\\path\\to\\bulk_playlist_results.csv"')
        raise SystemExit(2)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}")
        raise SystemExit(1)

    try:
        from app.core.database import SessionLocal
        from app.models.playlist import Playlist
        from app.models.spotify_account import SpotifyAccount
    except Exception as exc:
        print("Could not import app database/models. Run this from the project root.")
        print(f"Import error: {exc}")
        raise SystemExit(1)

    db = SessionLocal()
    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    try:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            for line_number, row in enumerate(reader, start=2):
                try:
                    account_id_raw = first_value(row, "account_id", "account id")
                    name = first_value(row, "name", "playlist", "playlist_name", "playlist name")
                    playlist_url = first_value(row, "playlist_url", "spotify_url", "link", "url")
                    spotify_id = extract_spotify_playlist_id(playlist_url)

                    if not account_id_raw or not name or not spotify_id:
                        skipped += 1
                        errors.append(
                            f"Line {line_number}: missing account_id, name, or valid playlist_url"
                        )
                        continue

                    account_id = int(account_id_raw)
                    account = db.query(SpotifyAccount).filter(SpotifyAccount.id == account_id).first()
                    if not account:
                        skipped += 1
                        errors.append(f"Line {line_number}: account_id {account_id} not found")
                        continue

                    playlist = (
                        db.query(Playlist)
                        .filter(Playlist.account_id == account_id, Playlist.spotify_id == spotify_id)
                        .first()
                    )

                    if playlist:
                        updated += 1
                    else:
                        playlist = Playlist(account_id=account_id, spotify_id=spotify_id, name=name)
                        created += 1

                    now = datetime.utcnow()
                    safe_set(playlist, "account_id", account_id)
                    safe_set(playlist, "spotify_id", spotify_id)
                    safe_set(playlist, "spotify_playlist_id", spotify_id)
                    safe_set(playlist, "name", name)
                    safe_set(playlist, "spotify_url", playlist_url)
                    safe_set(playlist, "external_url", playlist_url)
                    safe_set(playlist, "url", playlist_url)
                    safe_set(playlist, "playlist_url", playlist_url)
                    safe_set(playlist, "updated_at", now)

                    if not getattr(playlist, "created_at", None):
                        safe_set(playlist, "created_at", now)

                    db.add(playlist)

                except Exception as exc:
                    skipped += 1
                    errors.append(f"Line {line_number}: {exc}")

        db.commit()

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print("Import completed")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")

    if errors:
        print("\nWarnings:")
        for error in errors[:25]:
            print(f"- {error}")
        if len(errors) > 25:
            print(f"- ...and {len(errors) - 25} more")


if __name__ == "__main__":
    main()
