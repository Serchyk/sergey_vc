"""HTTP layer: FastAPI app exposing the deduplicated clients over HTTP."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sync_orders.fetcher import DEFAULT_SOURCE_URL, SourceError
from sync_orders.storage import Store
from sync_orders.synchronizer import Synchronizer

log = logging.getLogger("orders-sync")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

DB_PATH = os.environ.get("ORDERS_DB", "data/sync.db")
SOURCE_URL = os.environ.get("ORDERS_SOURCE_URL", DEFAULT_SOURCE_URL)
# 0 disables the background refresh; POST /sync always works regardless.
REFRESH_SECONDS = int(os.environ.get("ORDERS_REFRESH_SECONDS", "0") or 0)

store = Store(DB_PATH)
synchronizer = Synchronizer(store, source_url=SOURCE_URL)

app = FastAPI(
    title="Orders sync",
    description="Fetches raw orders, normalizes phones, deduplicates by phone.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _refresh_loop() -> None:
    while True:
        time.sleep(REFRESH_SECONDS)
        try:
            synchronizer.run()
        except SourceError as exc:
            log.warning("background sync failed: %s", exc)
        except Exception:  # noqa: BLE001 - never kill the loop
            log.exception("unexpected error during background sync")


@app.on_event("startup")
def _startup() -> None:
    """Make sure there is something to serve even before the first manual sync."""
    if store.load_result() is None:
        try:
            synchronizer.run()
            log.info("initial sync done")
        except SourceError as exc:
            log.warning("initial sync failed: %s", exc)
    if REFRESH_SECONDS > 0:
        threading.Thread(target=_refresh_loop, name="sync-loop", daemon=True).start()


def _result_payload() -> Dict[str, Any]:
    result = store.load_result()
    if result is None:
        raise HTTPException(status_code=503, detail="no data yet, call POST /sync")
    return result.as_dict()


@app.get("/result", tags=["public"])
@app.get("/api/result", tags=["public"])
@app.get("/clients", tags=["public"])
def get_result() -> JSONResponse:
    """The required payload: {clients: [...], stats: {...}}."""
    return JSONResponse(_result_payload())


@app.post("/sync", tags=["admin"])
@app.post("/api/sync", tags=["admin"])
def post_sync() -> JSONResponse:
    """Run a synchronization now. Safe to call repeatedly: the response is
    identical and no duplicate rows are created."""
    try:
        synchronizer.run()
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(_result_payload())


@app.get("/health", tags=["ops"])
def health() -> Dict[str, Any]:
    meta = store.meta()
    return {
        "status": "ok" if meta else "empty",
        "source_url": SOURCE_URL,
        "runs": meta.get("runs", 0),
        "synced_at": meta.get("synced_at", ""),
        "source_sha256": meta.get("source_sha256", ""),
        "background_refresh_seconds": REFRESH_SECONDS,
    }


@app.get("/admin/rejected", tags=["admin"])
def rejected() -> Dict[str, Any]:
    """Debug view of the dropped orders."""
    return {"rejected": store.invalid_orders()}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Single-page UI: controls on the left, output on the right."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")