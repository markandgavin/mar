from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# Pre-installed Chromium path for this environment; falls back to Playwright's own install
_CHROMIUM_EXEC = os.getenv(
    "CHROMIUM_EXECUTABLE",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
)

import aiofiles
import httpx
import yaml
from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext
from rapidfuzz import fuzz, process as fuzz_process
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .models import DocumentResult, PDFResponse, WebhookPayload
from .vendors.base import BaseVendor
from .vendors.shaw import ShawVendor
from .vendors.mohawk import MohawkVendor
from .vendors.daltile import DaltileVendor
from .vendors.armstrong import ArmstrongVendor
from .vendors.mannington import ManningtonVendor

CONFIG_PATH = Path(__file__).parent.parent / "config" / "vendors.yaml"
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", str(Path(__file__).parent.parent / "downloads")))

VENDOR_CLASSES: dict[str, type[BaseVendor]] = {
    "shaw": ShawVendor,
    "mohawk": MohawkVendor,
    "daltile": DaltileVendor,
    "armstrong": ArmstrongVendor,
    "mannington": ManningtonVendor,
}

_VENDOR_INSTANCES: dict[str, BaseVendor] = {}
_VENDOR_CONFIG: dict[str, Any] = {}


def load_vendor_config() -> dict[str, Any]:
    global _VENDOR_CONFIG
    if _VENDOR_CONFIG:
        return _VENDOR_CONFIG
    with open(CONFIG_PATH) as f:
        _VENDOR_CONFIG = yaml.safe_load(f).get("vendors", {})
    return _VENDOR_CONFIG


def get_vendor_instances() -> dict[str, BaseVendor]:
    global _VENDOR_INSTANCES
    if _VENDOR_INSTANCES:
        return _VENDOR_INSTANCES
    cfg = load_vendor_config()
    for vid, vcls in VENDOR_CLASSES.items():
        _VENDOR_INSTANCES[vid] = vcls(cfg.get(vid, {}))
    return _VENDOR_INSTANCES


def resolve_vendor(payload: WebhookPayload) -> tuple[Optional[BaseVendor], bool]:
    """Return (vendor_instance, was_inferred). Returns (None, False) if no match."""
    vendors = get_vendor_instances()
    cfg = load_vendor_config()

    # Explicit vendor_name wins
    if payload.vendor_name:
        name_lower = payload.vendor_name.lower().strip()
        for vid, vinst in vendors.items():
            vcfg = cfg.get(vid, {})
            aliases = [vinst.vendor_name.lower()] + [a.lower() for a in vcfg.get("aliases", [])]
            if any(name_lower == a or name_lower in a or a in name_lower for a in aliases):
                logger.info(f"Vendor resolved from payload.vendor_name: {vinst.vendor_name}")
                return vinst, False

    # Try to infer from the query prefix
    query_lower = payload.product_query.lower().strip()
    best_score = 0
    best_vendor: Optional[BaseVendor] = None

    for vid, vinst in vendors.items():
        vcfg = cfg.get(vid, {})
        aliases = [vinst.vendor_name.lower()] + [a.lower() for a in vcfg.get("aliases", [])]
        for alias in aliases:
            if query_lower.startswith(alias):
                score = len(alias)  # longer match = higher priority
                if score > best_score:
                    best_score = score
                    best_vendor = vinst
            else:
                ratio = fuzz.partial_ratio(alias, query_lower[:30])
                if ratio > 80 and ratio > best_score:
                    best_score = ratio
                    best_vendor = vinst

    if best_vendor:
        logger.info(f"Vendor inferred from query: {best_vendor.vendor_name} (score={best_score})")
        return best_vendor, True

    logger.info("No vendor resolved; will search all vendors")
    return None, False


def parse_product_code(payload: WebhookPayload) -> Optional[str]:
    """Return the most specific product code available."""
    if payload.product_code:
        return payload.product_code
    if payload.product_sku:
        return payload.product_sku
    # Extract trailing alphanumeric token from query
    tokens = payload.product_query.strip().split()
    for token in reversed(tokens):
        if re.match(r"^[A-Za-z0-9\-]{3,12}$", token):
            return token
    return None


def make_filename(vendor_id: str, product_code: Optional[str], doc_type: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    code_part = re.sub(r"[^A-Za-z0-9\-]", "_", product_code or "unknown")
    return f"{ts}_{vendor_id}_{code_part}_{doc_type}.pdf"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    reraise=True,
)
async def download_pdf(url: str, dest_path: Path) -> bool:
    """Download a PDF to dest_path. Returns True on success."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,*/*",
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=60, headers=headers
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                logger.warning(f"Unexpected content-type '{content_type}' for {url}")

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(dest_path, "wb") as f:
                await f.write(resp.content)

            logger.info(f"Downloaded {len(resp.content)} bytes -> {dest_path}")
            return True
    except Exception as exc:
        logger.error(f"Download failed for {url}: {exc}")
        raise


async def _process_vendor(
    vendor: BaseVendor,
    payload: WebhookPayload,
    product_code: Optional[str],
    browser: Browser,
) -> Optional[PDFResponse]:
    """Run the full search + extract + download pipeline for one vendor."""
    context: BrowserContext = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        java_script_enabled=True,
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()

    # Block images/fonts/media to speed up scraping
    await page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in ("image", "media", "font")
        else route.continue_(),
    )

    resp = PDFResponse(vendor=vendor.vendor_name, product_code=product_code)
    warnings: list[str] = []
    errors: list[str] = []

    try:
        product_info = await vendor.search_product(
            page,
            payload.product_query,
            product_code=product_code,
            color=payload.color,
        )

        if not product_info:
            logger.warning(f"{vendor.vendor_name}: product not found")
            return None

        resp.product_name = product_info.get("name")
        resp.product_url = product_info.get("url")
        resp.product_code = product_info.get("code") or product_code

        pdf_links = await vendor.get_pdf_links(page, product_info)

        # Score the match
        query_for_scoring = payload.product_query
        found_name = resp.product_name or ""
        sku_match = bool(product_code and product_code.lower() in found_name.lower())
        match_q, match_d = vendor.calculate_match_quality(
            query_for_scoring,
            found_name,
            sku_matched=sku_match,
        )
        resp.match_quality = match_q
        resp.match_details = match_d

        # Download each found PDF
        doc_map = {
            "warranty": "warranty",
            "spec_sheet": "spec_sheet",
            "care_maintenance": "care_maintenance",
            "installation_guide": "installation_guide",
            "color_reference": "color_reference",
        }
        for field, doc_type in doc_map.items():
            pdf_url = pdf_links.get(field)
            if not pdf_url:
                warnings.append(f"{doc_type}: not found on product page")
                logger.info(f"{vendor.vendor_name} - {doc_type}: no URL found")
                setattr(resp, field, DocumentResult(error="Not found on product page"))
                continue

            filename = make_filename(vendor.vendor_id, resp.product_code, doc_type)
            dest = DOWNLOADS_DIR / filename
            try:
                ok = await download_pdf(pdf_url, dest)
                if ok:
                    setattr(
                        resp,
                        field,
                        DocumentResult(
                            url=pdf_url,
                            local_path=str(dest),
                            filename=filename,
                            downloaded=True,
                        ),
                    )
                else:
                    setattr(resp, field, DocumentResult(url=pdf_url, error="Download returned False"))
            except Exception as exc:
                err_msg = f"{doc_type}: download failed - {exc}"
                errors.append(err_msg)
                logger.error(err_msg)
                setattr(resp, field, DocumentResult(url=pdf_url, error=str(exc)))

        resp.success = True
        resp.warnings = warnings
        resp.errors = errors
        return resp

    except Exception as exc:
        errors.append(str(exc))
        logger.error(f"{vendor.vendor_name} pipeline error: {exc}")
        resp.errors = errors
        return None
    finally:
        await context.close()


async def retrieve_flooring_pdfs(payload: WebhookPayload) -> PDFResponse:
    """Main entry point: resolve vendor(s), search, download PDFs, return response."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    product_code = parse_product_code(payload)
    vendor, was_inferred = resolve_vendor(payload)
    vendors_to_try: list[BaseVendor] = []

    if vendor:
        vendors_to_try = [vendor]
    else:
        # Search all vendors, pick best result
        vendors_to_try = list(get_vendor_instances().values())

    logger.info(
        f"Starting PDF retrieval | query={payload.product_query!r} "
        f"| code={product_code} | vendors={[v.vendor_name for v in vendors_to_try]}"
    )

    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    }
    if Path(_CHROMIUM_EXEC).exists():
        launch_kwargs["executable_path"] = _CHROMIUM_EXEC

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            results: list[PDFResponse] = []
            for v in vendors_to_try:
                try:
                    result = await _process_vendor(v, payload, product_code, browser)
                    if result:
                        if was_inferred:
                            result.match_quality = max(0.0, result.match_quality - 0.05)
                            result.match_details += ", vendor inferred"
                        results.append(result)
                        # If we got a high-confidence single-vendor match, stop early
                        if vendor and result.match_quality >= 0.7:
                            break
                except Exception as exc:
                    logger.error(f"Vendor {v.vendor_name} failed entirely: {exc}")

            if not results:
                return PDFResponse(
                    success=False,
                    errors=["No matching product found across all searched vendors"],
                    product_code=product_code,
                )

            # Pick the result with the highest match_quality
            best = max(results, key=lambda r: r.match_quality)
            return best

        finally:
            await browser.close()
