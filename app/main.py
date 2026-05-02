from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.api.routes import accounts, playlists, curation

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