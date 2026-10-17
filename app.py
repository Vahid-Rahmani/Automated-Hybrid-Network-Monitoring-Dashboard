from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import ROOT, load_config
from monitor import Monitor


def create_app(config=None, monitor=None, start_collector=True):
    config = config or load_config()
    monitor = monitor or Monitor(config)

    @asynccontextmanager
    async def lifespan(app):
        if start_collector:
            monitor.start()
        yield
        if start_collector:
            monitor.stop()

    application = FastAPI(title="Hybrid Lab Monitor", lifespan=lifespan)
    application.state.monitor = monitor
    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @application.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @application.get("/api/status")
    def api_status():
        return monitor.snapshot()

    @application.get("/api/devices")
    def api_devices():
        return monitor.snapshot()["devices"]

    @application.get("/health")
    def health():
        snapshot = monitor.snapshot()
        return {"api": "ok", "collector": "waiting" if not snapshot["updated_at"] else (
            "degraded" if snapshot["stale"] or snapshot["errors"] else "running"
        ), "updated_at": snapshot["updated_at"]}

    @application.get("/", response_class=FileResponse)
    def dashboard():
        return ROOT / "static" / "index.html"

    return application


app = create_app()
