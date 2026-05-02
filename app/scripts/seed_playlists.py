from app.core.database import SessionLocal
from app.models.playlist import Playlist
from app.models.follower_history import FollowerHistory


def seed():
    db = SessionLocal()

    # 🔥 delete in correct order
    db.query(FollowerHistory).delete()
    db.query(Playlist).delete()

    playlists = [
        # Account 1
        Playlist(account_id=1, name="Midnight Frequencies", followers=42810),
        Playlist(account_id=1, name="Deep Focus Sessions", followers=31200),
        Playlist(account_id=1, name="Afrobeats Rising", followers=19540),

        # Account 2
        Playlist(account_id=2, name="Rap Vibes", followers=18000),
        Playlist(account_id=2, name="Trap Nation", followers=22000),

        # Account 3
        Playlist(account_id=3, name="Gym Hits", followers=9000),
        Playlist(account_id=3, name="Power Mode", followers=12000),
    ]

    db.add_all(playlists)
    db.commit()
    db.close()

    print("✅ Seeded playlists successfully")


if __name__ == "__main__":
    seed()