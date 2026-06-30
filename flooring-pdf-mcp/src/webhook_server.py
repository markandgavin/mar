from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from .core import DOWNLOADS_DIR, get_vendor_instances, load_vendor_config, retrieve_flooring_pdfs
from .models import HealthResponse, PDFResponse, WebhookPayload

LOG_DIR = Path("/app/logs")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}",
        level="INFO",
    )
    logger.add(
        LOG_DIR / "flooring_mcp_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        compression="gz",
        level="DEBUG",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    load_vendor_config()
    get_vendor_instances()
    logger.info("Flooring PDF MCP server started")
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    logger.info("Flooring PDF MCP server shutting down")


app = FastAPI(
    title="Flooring PDF MCP Server",
    description="Retrieves manufacturer PDF documentation for flooring products",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    vendors = get_vendor_instances()
    return HealthResponse(
        status="ok",
        vendors_loaded=len(vendors),
        downloads_dir=str(DOWNLOADS_DIR),
    )


@app.post("/webhook", response_model=PDFResponse)
async def webhook(payload: WebhookPayload, request: Request) -> PDFResponse:
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        f"Webhook received | from={client_ip} | query={payload.product_query!r} "
        f"| vendor={payload.vendor_name} | sku={payload.product_sku} | code={payload.product_code}"
    )

    try:
        result = await retrieve_flooring_pdfs(payload)
        logger.info(
            f"Webhook complete | vendor={result.vendor} | product={result.product_name!r} "
            f"| match_quality={result.match_quality:.2f} | docs_found={result.docs_found()}/5"
        )
        return result
    except Exception as exc:
        logger.exception(f"Webhook handler error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"Unhandled exception on {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "errors": [str(exc)], "match_quality": 0.0},
    )
