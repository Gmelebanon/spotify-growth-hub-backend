from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.trade_service import (
    list_trades_for_account,
    create_trade_for_account,
    extend_trade,
    archive_trade,
    delete_trade,
    scan_trade,
    analyze_playlist_for_account,
    lookup_tracks_for_account,
)

router = APIRouter(tags=["Trades"])


class CreateTradePayload(BaseModel):
    track_name: str
    artist_name: str
    playlist_count: int
    placements: list[str]
    status: str = "active"


class AnalyzePlaylistPayload(BaseModel):
    playlist_url: str


class LookupTracksPayload(BaseModel):
    track_urls: list[str]


@router.get("/accounts/{account_id}/trades")
def get_trades(account_id: int, db: Session = Depends(get_db)):
    return list_trades_for_account(db, account_id)


@router.post("/accounts/{account_id}/trades")
def create_trade(
    account_id: int,
    payload: CreateTradePayload,
    db: Session = Depends(get_db),
):
    return create_trade_for_account(
        db=db,
        account_id=account_id,
        track_name=payload.track_name,
        artist_name=payload.artist_name,
        playlist_count=payload.playlist_count,
        placements=payload.placements,
        status=payload.status,
    )


@router.post("/accounts/{account_id}/trades/analyze-playlist")
def analyze_playlist(
    account_id: int,
    payload: AnalyzePlaylistPayload,
    db: Session = Depends(get_db),
):
    return analyze_playlist_for_account(
        db=db,
        account_id=account_id,
        playlist_url=payload.playlist_url,
    )


@router.post("/accounts/{account_id}/trades/lookup-tracks")
def lookup_tracks(
    account_id: int,
    payload: LookupTracksPayload,
    db: Session = Depends(get_db),
):
    return lookup_tracks_for_account(
        db=db,
        account_id=account_id,
        track_urls=payload.track_urls,
    )


@router.post("/trades/{trade_id}/extend")
def extend_trade_route(trade_id: int, db: Session = Depends(get_db)):
    trade = extend_trade(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.post("/trades/{trade_id}/archive")
def archive_trade_route(trade_id: int, db: Session = Depends(get_db)):
    trade = archive_trade(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.post("/trades/{trade_id}/scan")
def scan_trade_route(trade_id: int, db: Session = Depends(get_db)):
    trade = scan_trade(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.delete("/trades/{trade_id}")
def delete_trade_route(trade_id: int, db: Session = Depends(get_db)):
    ok = delete_trade(db, trade_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"success": True}