"""FastAPI application exposing RainTux control and status endpoints."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any
from urllib.parse import unquote

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from raintux.core.config import RainTuxConfig

log = logging.getLogger(__name__)


class SkinConfigUpdate(BaseModel):
    """Per-skin settings payload (Phase 1 subset)."""

    x: int | None = None
    y: int | None = None
    transparency: float | None = None
    click_through: bool | None = None


def build_app(engine: Any, cfg: RainTuxConfig) -> FastAPI:
    """Create the FastAPI app bound to a running :class:`~raintux.core.engine.RainTuxEngine`."""
    app = FastAPI(title="RainTux API", version="0.1.0")
    # The Skin Manager (Vite, :8080) is a different origin than the API (:7272);
    # allow loopback origins only so browsers can call us but random sites can't.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    app.state.engine = engine
    app.state.cfg = cfg
    app.state.ws_clients: set[WebSocket] = set()

    @app.get("/skins")
    async def list_skins() -> list[dict[str, str]]:
        return engine.list_installed_skins()

    @app.get("/skins/active")
    async def active_skins() -> list[dict[str, Any]]:
        return [{"id": sid, "path": str(rt.ini_path)} for sid, rt in engine.skins.items()]

    @app.post("/skins/{skin_path:path}/load")
    async def load_skin(skin_path: str) -> dict[str, str]:
        sid = unquote(skin_path).strip()
        try:
            await asyncio.to_thread(engine.load_skin, sid, None)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"status": "loaded", "id": sid}

    @app.post("/skins/{skin_path:path}/unload")
    async def unload_skin(skin_path: str) -> dict[str, str]:
        sid = unquote(skin_path).strip()
        await asyncio.to_thread(engine.unload_skin, sid)
        return {"status": "unloaded", "id": sid}

    @app.post("/skins/{skin_path:path}/refresh")
    async def refresh_skin(skin_path: str) -> dict[str, str]:
        sid = unquote(skin_path).strip()
        await asyncio.to_thread(engine.reload_skin, sid)
        return {"status": "refreshed", "id": sid}

    @app.get("/skins/{skin_path:path}/config")
    async def get_skin_config(skin_path: str) -> dict[str, Any]:
        sid = unquote(skin_path).strip()
        rt = engine.skins.get(sid)
        if not rt:
            raise HTTPException(status_code=404, detail="skin not loaded")
        return {
            "id": sid,
            "path": str(rt.ini_path),
            "compositor": engine.compositor.value,
        }

    @app.put("/skins/{skin_path:path}/config")
    async def put_skin_config(skin_path: str, body: SkinConfigUpdate) -> dict[str, str]:
        """TODO: apply X/Y / transparency to GTK window (Phase 1 stub)."""
        sid = unquote(skin_path).strip()
        if sid not in engine.skins:
            raise HTTPException(status_code=404, detail="skin not loaded")
        _ = body
        return {"status": "noop", "id": sid}

    @app.get("/system/status")
    async def system_status() -> dict[str, Any]:
        return {
            "compositor": engine.compositor.value,
            "skins_loaded": list(engine.skins.keys()),
            "api": {"host": cfg.api.host, "port": cfg.api.port},
        }

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        app.state.ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep-alive; clients may ping
        except Exception:
            pass
        finally:
            app.state.ws_clients.discard(websocket)

    return app


def start_api_server(engine: Any, cfg: RainTuxConfig) -> None:
    """Run uvicorn in a daemon thread (GTK remains on the main thread)."""
    app = build_app(engine, cfg)
    config = uvicorn.Config(app, host=cfg.api.host, port=cfg.api.port, log_level="warning")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, name="raintux-api", daemon=True)
    t.start()
    log.info("API listening on http://%s:%s", cfg.api.host, cfg.api.port)
