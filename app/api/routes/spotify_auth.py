from fastapi import APIRouter

router = APIRouter(prefix="/api/spotify", tags=["spotify-auth"])

@router.get("/login")
def spotify_login():
    return {"success": True}