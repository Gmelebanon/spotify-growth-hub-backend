import json
from datetime import datetime

from openai import OpenAI, APIError, APIConnectionError, RateLimitError
from fastapi import HTTPException

from app.core.config import settings
from app.schemas.ai import GenerateTitleResponse, TagTrackResponse

# Single shared client — instantiated once at import time
_client = OpenAI(api_key=settings.OPENAI_API_KEY)

# Use the cheapest capable model for short structured tasks
_MODEL = "gpt-4o-mini"

CURRENT_YEAR = datetime.now().year


def _call_openai(system_prompt: str, user_prompt: str, max_tokens: int = 60) -> str:
    """
    Shared wrapper for all OpenAI chat completions.
    Raises HTTPException on any OpenAI-side failure so routes
    never receive a raw SDK exception.
    """
    try:
        response = _client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0,        # deterministic output — no randomness
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
        )
        return response.choices[0].message.content.strip()

    except RateLimitError:
        raise HTTPException(
            status_code=429,
            detail="OpenAI rate limit reached. Please retry shortly.",
        )
    except APIConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Could not reach OpenAI API. Check network connectivity.",
        )
    except APIError as e:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI API error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error calling OpenAI: {str(e)}",
        )


def generate_playlist_title(keyword: str) -> GenerateTitleResponse:
    """
    Generate one clean, concise playlist title from a keyword.
    Prompt is kept minimal to reduce token cost and keep output predictable.
    """
    system_prompt = (
        "You are a music curator that writes short, clean playlist titles. "
        "Respond with only the playlist title — no punctuation, no quotes, no explanation."
    )

    user_prompt = (
        f"Create one playlist title for the keyword: '{keyword}'. "
        f"Include the year {CURRENT_YEAR}. "
        "Keep it under 8 words. Capitalise each word."
    )

    raw = _call_openai(system_prompt, user_prompt, max_tokens=30)

    # Strip any surrounding quotes the model may add despite instructions
    title = raw.strip('"').strip("'").strip()

    return GenerateTitleResponse(title=title)


def tag_track(track_name: str, artist_name: str) -> TagTrackResponse:
    """
    Return a structured tag set for a track: bpm_bucket, energy_level, genre.
    Response is requested as JSON to avoid parsing fragile free-text output.
    """
    system_prompt = (
        "You are a music metadata assistant. "
        "Always respond with a single valid JSON object and nothing else. "
        "No markdown, no code blocks, no explanation — only raw JSON."
    )

    user_prompt = (
        f"Tag this track:\n"
        f"Track: {track_name}\n"
        f"Artist: {artist_name}\n\n"
        "Return exactly this JSON structure:\n"
        "{\n"
        '  "bpm_bucket": one of "0-90" | "90-110" | "110-130" | "130+",\n'
        '  "energy_level": one of "low" | "medium" | "high",\n'
        '  "genre": one of "pop" | "hip-hop" | "afrobeats" | "house" | '
        '"r&b" | "rock" | "electronic" | "other"\n'
        "}"
    )

    raw = _call_openai(system_prompt, user_prompt, max_tokens=60)

    # Parse the JSON response and validate field values
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI returned non-JSON response for tag-track: {raw!r}",
        )

    # Normalise and validate each field — fall back to safe defaults if unexpected
    valid_bpm_buckets = {"0-90", "90-110", "110-130", "130+"}
    valid_energy_levels = {"low", "medium", "high"}
    valid_genres = {"pop", "hip-hop", "afrobeats", "house", "r&b", "rock", "electronic", "other"}

    bpm_bucket = data.get("bpm_bucket", "").strip()
    energy_level = data.get("energy_level", "").strip().lower()
    genre = data.get("genre", "").strip().lower()

    if bpm_bucket not in valid_bpm_buckets:
        bpm_bucket = "90-110"   # safe default

    if energy_level not in valid_energy_levels:
        energy_level = "medium"  # safe default

    if genre not in valid_genres:
        genre = "other"          # safe default

    return TagTrackResponse(
        bpm_bucket=bpm_bucket,
        energy_level=energy_level,
        genre=genre,
    )