from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import spotify_public
from app.api.routes import curation_history
from app.api.routes import curation_csv_playlists

from app.core.database import Base, engine
from app.api.routes import accounts, playlists, curation, curation_storage, playlist_manager_state, spotify_auth

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Spotify Growth Hub API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nerd-engine.vercel.app",
        "https://nerd-engine-git-main-wissammantoufeh-5383s-projects.vercel.app",
        "https://nerd-engine-96ldpmfe2-wissammantoufeh-5383s-projects.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(playlists.router)
app.include_router(curation.router)
app.include_router(curation_storage.router)
app.include_router(playlist_manager_state.router)
app.include_router(spotify_auth.router)
app.include_router(spotify_public.router)
app.include_router(curation_history.router)
app.include_router(curation_csv_playlists.router)