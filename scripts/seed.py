import sys
import os
from datetime import date, timedelta

# Allow running from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal, Base, engine
from app.models.playlist import Playlist
from app.models.follower_history import FollowerHistory

Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Clear existing seed data
db.query(FollowerHistory).delete()
db.query(Playlist).delete()
db.commit()

# Seed playlists
p1 = Playlist(name="Midnight Frequencies", followers=42810)
p2 = Playlist(name="Deep Focus Sessions", followers=31200)
p3 = Playlist(name="Afrobeats Rising", followers=19540)

db.add_all([p1, p2, p3])
db.commit()
db.refresh(p1)
db.refresh(p2)
db.refresh(p3)

p1_name, p1_id = p1.name, p1.id
p2_name, p2_id = p2.name, p2.id
p3_name, p3_id = p3.name, p3.id

# Seed follower history for p1 — 7 days of data
base_date = date.today() - timedelta(days=6)
history_data = [
    (p1_id, base_date + timedelta(days=0), 39200),
    (p1_id, base_date + timedelta(days=1), 39800),
    (p1_id, base_date + timedelta(days=2), 40500),
    (p1_id, base_date + timedelta(days=3), 41100),
    (p1_id, base_date + timedelta(days=4), 41600),
    (p1_id, base_date + timedelta(days=5), 42200),
    (p1_id, base_date + timedelta(days=6), 42810),
    (p2_id, base_date + timedelta(days=6), 31200),
    (p3_id, base_date + timedelta(days=0), 20100),
    (p3_id, base_date + timedelta(days=3), 19800),
    (p3_id, base_date + timedelta(days=6), 19540),
]

db.add_all([
    FollowerHistory(playlist_id=pid, date=d, followers=f)
    for pid, d, f in history_data
])
db.commit()
db.close()

print("Seed complete.")
print(f"  Playlists: {p1_name} (id={p1_id}), {p2_name} (id={p2_id}), {p3_name} (id={p3_id})")