from pydantic import BaseModel


class TrendItem(BaseModel):
    keyword: str
    trend_score: int
    growth_direction: str
    playlist_idea: str


class TrendsResponse(BaseModel):
    seed_keyword_used: str
    total_results: int
    results: list[TrendItem]