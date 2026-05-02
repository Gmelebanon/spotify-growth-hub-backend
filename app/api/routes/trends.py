from fastapi import APIRouter, Query

from app.schemas.trends import TrendsResponse
from app.services.trends_service import get_trends

router = APIRouter(prefix="/trends", tags=["Trends"])


@router.get("", response_model=TrendsResponse)
def trends(
    seed_keyword: str | None = Query(
        default=None,
        description="Seed keyword to fetch related rising queries from Google Trends.",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=25,
        description="Maximum number of trend results to return (1–25).",
    ),
) -> TrendsResponse:
    return get_trends(seed_keyword=seed_keyword, limit=limit)