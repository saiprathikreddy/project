"""
app/main.py — FastAPI application factory.

Routers are registered here in dependency order.
Each router is a separate file under app/routers/.
"""
from fastapi import FastAPI

from app.db.sqlite import init_db

app = FastAPI(
    title="CT-200 QA Backend",
    description="Parse medical device PDFs, manage document versions, generate QA test cases.",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    """Create SQLite tables on first run (idempotent)."""
    init_db()


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# Routers registered in build order — uncomment as each step is completed:
from app.routers import ingest, browse, selection, generate, retrieve
app.include_router(ingest.router,    prefix="/ingest",      tags=["ingest"])
app.include_router(browse.router,    prefix="",             tags=["browse"])
app.include_router(selection.router, prefix="/selections",  tags=["selection"])
app.include_router(generate.router,  prefix="/generations", tags=["generate"])
app.include_router(retrieve.router,  prefix="/generations", tags=["retrieve"])
