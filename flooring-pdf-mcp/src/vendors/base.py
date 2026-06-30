from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from loguru import logger
from playwright.async_api import Page
from rapidfuzz import fuzz


DOC_TYPE_KEYWORDS: dict[str, list[str]] = {
    "warranty": ["warranty", "limited warranty", "guarantee", "warrantee"],
    "spec_sheet": [
        "spec sheet", "specifications", "spec", "product data", "data sheet",
        "technical data", "technical spec", "product spec",
    ],
    "care_maintenance": [
        "care", "maintenance", "cleaning", "care guide", "cleaning guide",
        "floor care", "care and maintenance", "care & maintenance",
    ],
    "installation_guide": [
        "installation", "install", "installation guide", "instructions",
        "installation instructions", "how to install", "fitting", "setting guide",
    ],
    "color_reference": [
        "color", "colour", "swatch", "sample", "chip", "selection guide",
        "color chart", "colour chart", "catalog", "catalogue",
    ],
}


class BaseVendor(ABC):
    vendor_id: str
    vendor_name: str
    base_url: str
    aliases: list[str] = []
    rate_limit_delay: float = 2.0

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.rate_limit_delay = config.get("rate_limit_delay", self.rate_limit_delay)
        self._doc_keywords: dict[str, list[str]] = config.get(
            "document_keywords", {}
        )

    @abstractmethod
    async def search_product(
        self, page: Page, query: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        """Search for a product. Returns dict with at minimum 'url' and 'name' keys."""

    @abstractmethod
    async def get_pdf_links(
        self, page: Page, product_info: dict[str, Any]
    ) -> dict[str, Optional[str]]:
        """Return mapping of doc_type -> PDF URL (or None) for each document type."""

    async def _rate_limit(self) -> None:
        await asyncio.sleep(self.rate_limit_delay)

    def classify_pdf_link(self, text: str, href: str) -> Optional[str]:
        """Return the doc_type that best matches a link's text/href, or None."""
        combined = f"{text} {href}".lower()
        scores: dict[str, float] = {}
        for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
            vendor_kw = self._doc_keywords.get(doc_type, [])
            all_kw = keywords + vendor_kw
            best = max(
                fuzz.partial_ratio(kw, combined) for kw in all_kw
            )
            scores[doc_type] = best

        best_type = max(scores, key=lambda k: scores[k])
        if scores[best_type] >= 70:
            return best_type
        return None

    def calculate_match_quality(
        self,
        query: str,
        found_name: str,
        sku_matched: bool = False,
        code_matched: bool = False,
        vendor_inferred: bool = False,
    ) -> tuple[float, str]:
        """Score how well found_name matches the original query (0.0–1.0)."""
        name_score = fuzz.token_sort_ratio(query.lower(), found_name.lower()) / 100.0
        details: list[str] = [f"name similarity {name_score:.0%}"]

        bonus = 0.0
        if sku_matched:
            bonus += 0.25
            details.append("SKU matched")
        if code_matched:
            bonus += 0.20
            details.append("product code matched")
        if vendor_inferred:
            bonus -= 0.05
            details.append("vendor inferred")

        score = min(1.0, name_score + bonus)
        return round(score, 3), ", ".join(details)

    def is_pdf_url(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(".pdf") or "pdf" in path

    def resolve_url(self, href: str, page_url: str) -> str:
        if href.startswith("http"):
            return href
        return urljoin(page_url, href)

    async def extract_pdf_links_from_page(
        self, page: Page
    ) -> dict[str, Optional[str]]:
        """Generic extractor: scrape all <a> hrefs ending in .pdf and classify them."""
        results: dict[str, Optional[str]] = {
            "warranty": None,
            "spec_sheet": None,
            "care_maintenance": None,
            "installation_guide": None,
            "color_reference": None,
        }
        try:
            anchors = await page.query_selector_all("a[href]")
            for anchor in anchors:
                href = await anchor.get_attribute("href") or ""
                text = (await anchor.inner_text()).strip()

                if not href:
                    continue

                full_url = self.resolve_url(href, page.url)

                if not (self.is_pdf_url(full_url) or self.is_pdf_url(href)):
                    continue

                doc_type = self.classify_pdf_link(text, href)
                if doc_type and results.get(doc_type) is None:
                    results[doc_type] = full_url
                    logger.debug(
                        "Classified link",
                        doc_type=doc_type,
                        text=text[:60],
                        url=full_url[:80],
                    )
        except Exception as exc:
            logger.warning(f"Error extracting PDF links: {exc}")
        return results

    @staticmethod
    def extract_code_from_query(query: str) -> Optional[str]:
        """Try to pull a product code (alphanumeric, often trailing) from the query."""
        tokens = query.strip().split()
        for token in reversed(tokens):
            if re.match(r"^[A-Za-z0-9\-]{3,12}$", token):
                return token
        return None

    @staticmethod
    def strip_vendor_prefix(query: str, vendor_name: str) -> str:
        """Remove leading vendor name from query for cleaner product search."""
        pattern = re.compile(re.escape(vendor_name), re.IGNORECASE)
        return pattern.sub("", query).strip()
