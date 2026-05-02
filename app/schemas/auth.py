from datetime import datetime

from pydantic import BaseModel


class SpotifyAccountItem(BaseModel):
    id: int
    spotify_user_id: str
    display_name: str | None
    expires_at: datetime
    token_expired: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuthCallbackResponse(BaseModel):
    message: str
    account_id: int
    spotify_user_id: str
    display_name: str | None
    expires_at: datetime


class AccountStatusResponse(BaseModel):
    connected: bool
    account_id: int
    spotify_user_id: str
    display_name: str | None
    expires_at: datetime
    token_expired: bool


class RefreshResponse(BaseModel):
    message: str
    account_id: int
    spotify_user_id: str
    display_name: str | None
    expires_at: datetime
    token_expired: bool