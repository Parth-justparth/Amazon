"""FastAPI application entry point.

Boots the SecondLife AI backend: seeds the demo database on startup, mounts the
``Return_Initiation_Service`` router, exposes a health probe, and serves a
minimal static test page at ``/ui`` for manually exercising the API.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.fixtures.loader import seed_on_startup
from app.services.bank_details import router as bank_details_router
from app.services.carbon_savings import router as carbon_savings_router
from app.services.condition_assessment import router as condition_assessment_router
from app.services.decision_engine import router as decision_engine_router
from app.services.green_points import router as green_points_router
from app.services.keep_it import router as keep_it_router
from app.services.refund import router as refund_router
from app.services.return_initiation import router as return_initiation_router
from app.services.warehouse_flow import router as warehouse_router
from app.services.marketplace import router as marketplace_router
from app.services.resale_flow import router as resale_router
from app.services.donation_flow import router as donation_router
from app.services.return_flow import router as return_flow_router

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables and seed the demo dataset before serving requests."""

    seed_on_startup()
    yield


app = FastAPI(
    title="Amazon SecondLife AI",
    version=__version__,
    description="Return-interception system that selects the optimal disposition for a returned item.",
    lifespan=lifespan,
)

app.include_router(return_initiation_router)
app.include_router(condition_assessment_router)
app.include_router(decision_engine_router)
app.include_router(keep_it_router)
app.include_router(refund_router)
app.include_router(green_points_router)
app.include_router(carbon_savings_router)
app.include_router(bank_details_router)
app.include_router(warehouse_router)
app.include_router(marketplace_router)
app.include_router(resale_router)
app.include_router(donation_router)
app.include_router(return_flow_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by smoke tests and orchestration."""

    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the minimal manual-testing page at the site root."""

    return FileResponse(_STATIC_DIR / "index.html")


# Mount the static test page (vanilla HTML+JS, no build step) at /static.
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
