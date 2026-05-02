from fastapi import APIRouter

from app.schemas.ai import (
    GenerateTitleRequest,
    GenerateTitleResponse,
    TagTrackRequest,
    TagTrackResponse,
)
from app.services.ai_service import generate_playlist_title, tag_track

router = APIRouter(prefix="/ai", tags=["AI Helpers"])


@router.post("/generate-title", response_model=GenerateTitleResponse)
def ai_generate_title(payload: GenerateTitleRequest) -> GenerateTitleResponse:
    """
    Generate one clean playlist title from a seed keyword.
    Uses OpenAI gpt-4o-mini. No database reads or writes.
    """
    return generate_playlist_title(payload.keyword)


@router.post("/tag-track", response_model=TagTrackResponse)
def ai_tag_track(payload: TagTrackRequest) -> TagTrackResponse:
    """
    Return structured metadata tags for a track: bpm_bucket, energy_level, genre.
    Uses OpenAI gpt-4o-mini. No database reads or writes.
    """
    return tag_track(payload.track_name, payload.artist_name)