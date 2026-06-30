"""
MCP server exposing flooring PDF retrieval as a tool callable by AI assistants.
Run alongside the webhook server or standalone via stdio transport.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from .core import retrieve_flooring_pdfs
from .models import WebhookPayload

# Suppress loguru to stderr when running as stdio MCP (would corrupt the JSON stream)
logger.remove()
logger.add(Path("/app/logs/mcp_server.log"), level="DEBUG", rotation="1 day", retention="7 days")

server = Server("flooring-pdf-mcp")


@server.list_tools()
async def list_tools() -> ListToolsResult:
    return ListToolsResult(
        tools=[
            Tool(
                name="get_flooring_pdfs",
                description=(
                    "Retrieve manufacturer PDF documentation (warranty, spec sheet, "
                    "care/maintenance manual, installation guide, color reference) "
                    "for a flooring product. Supported vendors: Shaw, Mohawk, Daltile, "
                    "Armstrong, Mannington."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "product_query": {
                            "type": "string",
                            "description": (
                                "Product name and/or code, e.g. 'Shaw 00134' or "
                                "'Mohawk Revwood Blackthorn 7894'. Required."
                            ),
                        },
                        "vendor_name": {
                            "type": "string",
                            "description": "Vendor name if known (Shaw, Mohawk, Daltile, Armstrong, Mannington).",
                        },
                        "product_sku": {
                            "type": "string",
                            "description": "Product SKU if known.",
                        },
                        "product_code": {
                            "type": "string",
                            "description": "Product code if known.",
                        },
                        "color": {
                            "type": "string",
                            "description": "Color name or code.",
                        },
                    },
                    "required": ["product_query"],
                },
            ),
            Tool(
                name="list_supported_vendors",
                description="List all supported flooring vendors.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]
    )


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    if name == "list_supported_vendors":
        from .core import get_vendor_instances
        vendors = get_vendor_instances()
        content = "\n".join(f"- {v.vendor_name}" for v in vendors.values())
        return CallToolResult(content=[TextContent(type="text", text=content)])

    if name == "get_flooring_pdfs":
        try:
            payload = WebhookPayload(**arguments)
        except Exception as exc:
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=f"Invalid arguments: {exc}")],
            )

        try:
            result = await retrieve_flooring_pdfs(payload)
            return CallToolResult(
                content=[TextContent(type="text", text=result.model_dump_json(indent=2))]
            )
        except Exception as exc:
            logger.exception(f"MCP tool error: {exc}")
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=f"Error: {exc}")],
            )

    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=f"Unknown tool: {name}")],
    )


async def run_mcp_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run_mcp_server())
