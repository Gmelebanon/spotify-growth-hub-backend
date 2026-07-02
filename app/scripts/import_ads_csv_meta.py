"""
Fast one-time local importer for Ads CSV metadata.

Run from backend repo only if you have DATABASE_URL locally:

    python scripts/import_ads_csv_meta.py

For Render, prefer the API endpoint:
    POST /api/ads/settings/import-csv-meta
"""

from app.api.routes.ads_settings import import_ads_settings_meta_from_csv_seed

if __name__ == "__main__":
    print(import_ads_settings_meta_from_csv_seed())
