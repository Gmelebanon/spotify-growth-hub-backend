from sqlalchemy import text
from app.core.database import SessionLocal

def main():
    db = SessionLocal()

    try:
        print("Deleting ALL rows from public.follower_history...")
        print("This will remove every follower history row.")

        db.execute(text("TRUNCATE TABLE public.follower_history RESTART IDENTITY CASCADE;"))
        db.commit()

        print("Done. public.follower_history is empty.")

    except Exception as exc:
        db.rollback()
        print("Failed:", exc)

    finally:
        db.close()

if __name__ == "__main__":
    main()