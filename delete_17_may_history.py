from sqlalchemy import text
from app.core.database import SessionLocal

TARGET_DATE = "2026-05-17"

def main():
    db = SessionLocal()

    try:
        print(f"Deleting follower_history rows for {TARGET_DATE}...")

        result = db.execute(
            text("""
                DELETE FROM public.follower_history
                WHERE COALESCE(date, created_at::date) = :target_date
            """),
            {"target_date": TARGET_DATE},
        )

        db.commit()
        print(f"Done. Deleted rows: {result.rowcount}")

    except Exception as exc:
        db.rollback()
        print("Failed:", exc)

    finally:
        db.close()

if __name__ == "__main__":
    main()