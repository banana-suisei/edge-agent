"""Image fetching tool for calling a configured HTTP endpoint."""

from __future__ import annotations

import base64

import requests
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command


def create_fetch_image_tool(url: str, timeout: int = 30):
    """Create a fetch_image tool bound to the given HTTP endpoint."""

    @tool
    def fetch_image(runtime: ToolRuntime) -> Command:
        """Fetch an image from the configured local HTTP endpoint for visual analysis.

        Use this when you need to view or analyze the current image from the local service.
        """
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()

        mime_type = resp.headers.get("content-type", "image/jpeg")
        image_b64 = base64.b64encode(resp.content).decode("utf-8")
        size_kb = len(resp.content) / 1024

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Fetched image from {url} ({mime_type}, {size_kb:.1f} KB).",
                        tool_call_id=runtime.tool_call_id,
                    ),
                    HumanMessage(
                        content=[
                            {"type": "text", "text": "[Image from local service]"},
                            {"type": "image", "base64": image_b64, "mime_type": mime_type},
                        ]
                    ),
                ]
            },
        )

    return fetch_image
