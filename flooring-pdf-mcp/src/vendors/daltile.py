from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger
from playwright.async_api import Page
from rapidfuzz import fuzz, process as fuzz_process

from .base import BaseVendor


class DaltileVendor(BaseVendor):
    vendor_id = "daltile"
    vendor_name = "Daltile"
    base_url = "https://www.daltile.com"
    aliases = ["dal-tile", "dal tile", "daltile tile"]

    async def search_product(
        self, page: Page, query: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        product_code = kwargs.get("product_code") or self.extract_code_from_query(query)
        clean_query = self.strip_vendor_prefix(query, "daltile")

        # Daltile uses a faceted search
        search_url = f"{self.base_url}/products/search/{clean_query.replace(' ', '%20')}"
        logger.info(f"Daltile search: {search_url}")

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await self._rate_limit()

            await page.wait_for_selector(
                "[class*='product'], [class*='tile-card'], [class*='ProductCard']",
                timeout=15000,
            )

            candidates: list[dict[str, Any]] = []
            card_selectors = [
                "[class*='product-card'] a",
                "[class*='tile-card'] a",
                "[class*='ProductCard'] a",
                ".product-listing a",
                "[data-testid*='product'] a",
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

            if not candidates:
                logger.warning("Daltile: no product cards found")
                return None

            best = self._best_candidate(clean_query, product_code, candidates)
            if best:
                url = self.resolve_url(best["url"], search_url)
                return {"name": best["name"], "url": url, "code": product_code}

        except Exception as exc:
            logger.error(f"Daltile search failed: {exc}")
        return None

    async def get_pdf_links(
        self, page: Page, product_info: dict[str, Any]
    ) -> dict[str, Optional[str]]:
        try:
            await page.goto(product_info["url"], wait_until="domcontentloaded", timeout=30000)
            await self._rate_limit()

            # Daltile has a "Documents & Downloads" section
            for tab_text in ["Documents", "Downloads", "Resources", "Specs"]:
                try:
                    tab = page.get_by_text(tab_text, exact=False)
                    if await tab.count() > 0:
                        await tab.first.click()
                        await asyncio.sleep(1.5)
                        break
                except Exception:
                    pass

            return await self.extract_pdf_links_from_page(page)
        except Exception as exc:
            logger.error(f"Daltile get_pdf_links failed: {exc}")
            return {}

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
