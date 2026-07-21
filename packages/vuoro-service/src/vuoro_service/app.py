"""FastAPI application factory with bootstrap-only operational probes."""

from __future__ import annotations

from fastapi import FastAPI

from vuoro_service import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Vuoro service", version=__version__)

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, object]:
        return {
            "status": "not-ready",
            "reason": "no domain adapters are registered in the bootstrap release",
        }

    return app
