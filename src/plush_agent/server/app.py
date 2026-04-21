from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from plush_agent.server.routes_chat import router as chat_router
from plush_agent.server.routes_form import router as form_router
from plush_agent.server.routes_approval import router as approval_router
from plush_agent.server.session import SessionManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    uds_server = getattr(app.state, "uds_server", None)
    if uds_server:
        await uds_server.start()
    yield
    if uds_server:
        await uds_server.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Plush Agent", version="0.1.0", lifespan=lifespan)
    app.include_router(chat_router, prefix="/api")
    app.include_router(form_router, prefix="/api")
    app.include_router(approval_router, prefix="/api")
    app.state.session_manager = SessionManager()
    return app
