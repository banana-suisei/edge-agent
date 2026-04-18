from __future__ import annotations

from fastapi import FastAPI

from plush_agent.server.routes_chat import router as chat_router
from plush_agent.server.routes_form import router as form_router
from plush_agent.server.routes_approval import router as approval_router


def create_app() -> FastAPI:
    app = FastAPI(title="Plush Agent", version="0.1.0")
    app.include_router(chat_router, prefix="/api")
    app.include_router(form_router, prefix="/api")
    app.include_router(approval_router, prefix="/api")
    return app
