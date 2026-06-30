"""
Entrypoint for the Flooring PDF MCP webhook server.
Runs uvicorn on port 5000 (or $PORT).

Usage:
    python main.py               # HTTP webhook server on :5000
    python main.py --mcp         # MCP stdio server (for AI assistant integration)
    python -m tests.test_mode    # Test mode with hardcoded payloads
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Flooring PDF MCP Server")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as MCP stdio server instead of HTTP webhook server",
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--reload", action="store_true", help="Enable hot-reload (dev only)")
    args = parser.parse_args()

    if args.mcp:
        from src.mcp_server import run_mcp_server
        asyncio.run(run_mcp_server())
    else:
        import uvicorn
        uvicorn.run(
            "src.webhook_server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )


if __name__ == "__main__":
    main()
