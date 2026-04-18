from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from plush_agent.config import Config
from plush_agent.server.app import create_app


def run_server(config: Config) -> None:
    from plush_agent.agent import build_agent
    from plush_agent.hitl.approval_handler import ApprovalHandler

    agent, store, checkpointer = build_agent(config)
    handler = ApprovalHandler(agent, config)

    app = create_app()
    app.state.approval_handler = handler
    app.state.store = store
    app.state.checkpointer = checkpointer
    app.state.config = config

    uvicorn.run(app, host=config.server.host, port=config.server.port)
