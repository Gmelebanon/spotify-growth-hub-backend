from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.core.database import Base, engine

from app.api.routes import accounts
from app.api.routes import ads_filter_options
from app.api.routes import ads_settings
from app.api.routes import artist_library
from app.api.routes import curation
from app.api.routes import curation_csv_playlists
from app.api.routes import curation_history
from app.api.routes import curation_storage
from app.api.routes import playlist_manager_state
from app.api.routes import playlists
from app.api.routes import production_mashups
from app.api.routes import production_smart_segments
from app.api.routes import scheduling
from app.api.routes import settings
from app.api.routes import spotify_auth
from app.api.routes import spotify_public
from app.api.routes import sync_status
from app.api.routes import trends
from app.api.routes.song_metrics import router as song_metrics_router

load_dotenv()

Base.metadata.create_all(bind=engine)

ALLOWED_ORIGINS = {
    "https://nerd-engine.vercel.app",
    "https://nerd-engine-git-main-wissammantoufeh-5383s-projects.vercel.app",
    "https://nerd-engine-96ldpmfe2-wissammantoufeh-5383s-projects.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
}


def cors_origin(origin: str | None) -> str:
    if not origin:
        return "*"

    if origin in ALLOWED_ORIGINS:
        return origin

    if origin.startswith("https://") and origin.endswith(".vercel.app"):
        return origin

    if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
        return origin

    return "https://nerd-engine.vercel.app"


def add_cors_headers(response: Response, origin: str | None) -> Response:
    response.headers["Access-Control-Allow-Origin"] = cors_origin(origin)
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Vary"] = "Origin"
    return response


app = FastAPI(
    title="Spotify Growth Hub API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_origin_regex=r"https://.*\.vercel\.app|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def force_cors_on_all_responses(request: Request, call_next):
    origin = request.headers.get("origin")

    if request.method == "OPTIONS":
        return add_cors_headers(Response(status_code=204), origin)

    try:
        response = await call_next(request)
        return add_cors_headers(response, origin)
    except Exception as exc:
        # This prevents browser CORS masking when a route crashes.
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error": str(exc),
                "path": request.url.path,
            },
        )
        return add_cors_headers(response, origin)


app.include_router(accounts.router)
app.include_router(playlists.router)
app.include_router(curation.router)
app.include_router(curation_storage.router)
app.include_router(playlist_manager_state.router)
app.include_router(spotify_auth.router)
app.include_router(spotify_public.router)
app.include_router(curation_history.router)
app.include_router(curation_csv_playlists.router)
app.include_router(ads_settings.router)
app.include_router(ads_filter_options.router)
app.include_router(artist_library.router)
app.include_router(song_metrics_router)
app.include_router(settings.router)
app.include_router(production_smart_segments.router)
app.include_router(production_mashups.router)
app.include_router(scheduling.router)
app.include_router(sync_status.router)
app.include_router(trends.router)


@app.get("/")
def root():
    return {"ok": True, "service": "Spotify Growth Hub API"}


@app.get("/health")
def health():
    return {"ok": True}
