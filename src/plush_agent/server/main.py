from __future__ import annotations

import uvicorn

from plush_agent.config import Config
from plush_agent.server.app import create_app


def run_server(config: Config) -> None:
    from plush_agent.agent import build_agent
    from plush_agent.hitl.approval_handler import ApprovalHandler
    from plush_agent.hitl.streaming_handler import StreamingApprovalHandler

    agent, store, checkpointer = build_agent(config)
    handler = ApprovalHandler(agent, config)
    streaming_handler = StreamingApprovalHandler(agent, config)

    app = create_app()
    app.state.approval_handler = handler
    app.state.streaming_approval_handler = streaming_handler
    app.state.store = store
    app.state.checkpointer = checkpointer
    app.state.config = config

    if config.uds.enabled:
        from plush_agent.server.uds_server import UdsServer
        uds = UdsServer(
            config=config,
            approval_handler=handler,
            streaming_handler=streaming_handler,
            session_manager=app.state.session_manager,
        )
        app.state.uds_server = uds

    uvicorn.run(app, host=config.server.host, port=config.server.port)
