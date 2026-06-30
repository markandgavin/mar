from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from loguru import logger
from playwright.async_api import Page
from rapidfuzz import fuzz, process as fuzz_process

from .base import BaseVendor

# Known static document URLs scraped from altro.com CDN.
# These are product-line-wide (not color-specific) and stable.
_STATIC_DOCS: dict[str, dict[str, str]] = {
    "classic-25": {
        "spec_sheet": (
            "https://www.altro.com/getmedia/bc17c707-777d-40ca-945e-c6a4dbf5cec6/"
            "Altro-Technical-Data-Sheet-Classic25-2023.pdf?ext=.pdf"
        ),
        "installation_guide": (
            "https://www.altro.com/getmedia/7b5b0ae8-fdb3-4229-862c-0f7abbd11237/"
            "Altro-flooring-installation-guide_2021.pdf"
        ),
        "care_maintenance": (
            "https://www.altro.com/getmedia/734481da-f423-44a5-a1cf-a2a266826bfc/"
            "Cleaning-guide-for-safety-and-slip-resistant-flooring-2020.pdf"
        ),
        "warranty": (
            "https://www.altro.com/getmedia/4d1137b1-fafa-421e-9978-7fe63fb93178/"
            "Altro-Product-Warranty.pdf?ext=.pdf"
        ),
    },
}

# Shared warranty and installation docs apply to all Altro flooring lines
_SHARED_DOCS: dict[str, str] = {
    "warranty": (
        "https://www.altro.com/getmedia/4d1137b1-fafa-421e-9978-7fe63fb93178/"
        "Altro-Product-Warranty.pdf?ext=.pdf"
    ),
    "installation_guide": (
        "https://www.altro.com/getmedia/7b5b0ae8-fdb3-4229-862c-0f7abbd11237/"
        "Altro-flooring-installation-guide_2021.pdf"
    ),
    "care_maintenance": (
        "https://www.altro.com/getmedia/734481da-f423-44a5-a1cf-a2a266826bfc/"
        "Cleaning-guide-for-safety-and-slip-resistant-flooring-2020.pdf"
    ),
}

# Map query keywords -> product slug for static doc lookup
_PRODUCT_SLUG_MAP: dict[str, str] = {
    "classic 25": "classic-25",
    "classic25": "classic-25",
    "x25": "classic-25",
}


def _query_to_slug(query: str) -> Optional[str]:
    q = query.lower()
    for keyword, slug in _PRODUCT_SLUG_MAP.items():
        if keyword in q:
            return slug
    return None


class AltroVendor(BaseVendor):
    vendor_id = "altro"
    vendor_name = "Altro"
    base_url = "https://www.altro.com"
    aliases = [
        "altro floors", "altro flooring", "altro safety flooring",
        "altro us", "altro uk", "altrofloors",
    ]

    async def search_product(
        self, page: Page, query: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        product_code = kwargs.get("product_code") or self.extract_code_from_query(query)
        color = kwargs.get("color", "")
        clean_query = self.strip_vendor_prefix(query, "altro").strip()

        # Try to identify the product line from the query for static doc lookup
        slug = _query_to_slug(clean_query)

        # Build search URL - Altro uses /us/search
        search_url = (
            f"{self.base_url}/us/search"
            f"#{clean_query.replace(' ', '%20')}"
        )
        logger.info(f"Altro search: {search_url}")

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await self._rate_limit()

            # Altro search results use various selectors
            candidates: list[dict[str, Any]] = []
            card_selectors = [
                "[class*='product-card'] a",
                "[class*='ProductCard'] a",
                "[class*='product-tile'] a",
                "[class*='search-result'] a",
                "article a",
                ".product a",
            ]
            for sel in card_selectors:
                cards = await page.query_selector_all(sel)
                if cards:
                    for card in cards[:10]:
                        href = await card.get_attribute("href") or ""
                        text = (await card.inner_text()).strip()
                        if href and text and len(text) > 3:
                            candidates.append({"name": text, "url": href})
                    if candidates:
                        break

            if candidates:
                best = self._best_candidate(clean_query, product_code, candidates)
                if best:
                    url = self.resolve_url(best["url"], search_url)
                    return {
                        "name": best["name"],
                        "url": url,
                        "code": product_code,
                        "slug": slug,
                    }

            # Fallback: construct product URL directly if slug is known
            if slug:
                color_slug = (color or "").lower().replace(" ", "-") or "pewter-grey"
                product_url = f"{self.base_url}/us/products/{slug}/{color_slug}"
                logger.info(f"Altro: falling back to direct URL {product_url}")
                return {
                    "name": f"Altro {clean_query}",
                    "url": product_url,
                    "code": product_code,
                    "slug": slug,
                }

        except Exception as exc:
            logger.error(f"Altro search failed: {exc}")
            # Still return static info if we know the product slug
            if slug:
                return {
                    "name": f"Altro {clean_query}",
                    "url": f"{self.base_url}/us/products/{slug}",
                    "code": product_code,
                    "slug": slug,
                }
        return None

    async def get_pdf_links(
        self, page: Page, product_info: dict[str, Any]
    ) -> dict[str, Optional[str]]:
        slug = product_info.get("slug")

        # Start with static known docs for this product line
        results: dict[str, Optional[str]] = dict(_SHARED_DOCS)
        if slug and slug in _STATIC_DOCS:
            results.update(_STATIC_DOCS[slug])

        # Attempt live scrape to supplement / override with page-specific links
        try:
            await page.goto(product_info["url"], wait_until="domcontentloaded", timeout=30000)
            await self._rate_limit()

            for tab_text in ["Documents", "Technical", "Downloads", "Resources"]:
                try:
                    tab = page.get_by_text(tab_text, exact=False)
                    if await tab.count() > 0:
                        await tab.first.click()
                        await asyncio.sleep(1.5)
                        break
                except Exception:
                    pass

            live = await self.extract_pdf_links_from_page(page)
            # Merge: live links take precedence over static fallbacks
            for doc_type, url in live.items():
                if url:
                    results[doc_type] = url
        except Exception as exc:
            logger.warning(f"Altro live scrape failed, using static docs: {exc}")

        return results

    def _best_candidate(
        self,
        query: str,
        code: Optional[str],
        candidates: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if not candidates:
            return None
        if code:
            for c in candidates:
                if code.lower() in c["name"].lower() or code.lower() in c["url"].lower():
                    return c
        names = [c["name"] for c in candidates]
        match = fuzz_process.extractOne(query, names, scorer=fuzz.token_sort_ratio)
        if match and match[1] >= 40:
            return candidates[names.index(match[0])]
        return candidates[0]
