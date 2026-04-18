from __future__ import annotations

import json
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient


async def load_mcp_tools(config_path: str | Path) -> tuple[list, MultiServerMCPClient | None]:
    path = Path(config_path)
    if not path.exists():
        return [], None
    with open(path) as f:
        servers_config = json.load(f)
    if not servers_config:
        return [], None
    client = MultiServerMCPClient(servers_config)
    tools = await client.get_tools()
    return tools, client
