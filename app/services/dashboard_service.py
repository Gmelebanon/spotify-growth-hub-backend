from sqlalchemy.orm import Session

from app.models.playlist import Playlist
from app.models.follower_history import FollowerHistory


def get_dashboard_summary_for_account(db: Session, account_id: int) -> dict:
    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.id.asc())
        .all()
    )

    total_playlists = len(playlists)
    total_followers = sum(p.followers for p in playlists)

    total_growth_last_24h = 0

    for playlist in playlists:
        history = (
            db.query(FollowerHistory)
            .filter(FollowerHistory.playlist_id == playlist.id)
            .order_by(FollowerHistory.date.asc())
            .all()
        )

        if len(history) >= 2:
            total_growth_last_24h += history[-1].followers - history[-2].followers
        else:
            total_growth_last_24h += 0

    return {
        "total_playlists": total_playlists,
        "total_followers": total_followers,
        "total_growth_last_24h": total_growth_last_24h,
    }


def get_top_playlists_for_account(db: Session, account_id: int, limit: int = 5) -> dict:
    playlists = (
        db.query(Playlist)
        .filter(Playlist.account_id == account_id)
        .order_by(Playlist.id.asc())
        .all()
    )

    results = []

    for playlist in playlists:
        history = (
            db.query(FollowerHistory)
            .filter(FollowerHistory.playlist_id == playlist.id)
            .order_by(FollowerHistory.date.asc())
            .all()
        )

        growth = 0
        if len(history) >= 2:
            growth = history[-1].followers - history[0].followers

        results.append(
            {
                "playlist_id": playlist.id,
                "spotify_playlist_id": playlist.spotify_playlist_id,
                "name": playlist.name,
                "followers": playlist.followers,
                "growth": growth,
                "created_at": playlist.created_at,
            }
        )

    results.sort(key=lambda x: x["growth"], reverse=True)

    return {
        "limit": limit,
        "results": results[:limit],
    }