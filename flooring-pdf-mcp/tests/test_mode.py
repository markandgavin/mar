"""
Test mode: runs hardcoded payloads against real Shaw, Mohawk, and Daltile pages.
Usage:
    python -m tests.test_mode
    python -m tests.test_mode --vendor shaw
    python -m tests.test_mode --dry-run   # skip actual scraping, just log config
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from src.core import retrieve_flooring_pdfs
from src.models import WebhookPayload

logger.remove()
logger.add(sys.stderr, level="DEBUG", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")

TEST_PAYLOADS: list[dict] = [
    # Shaw — hardwood plank product with known code
    {
        "label": "Shaw - Floorté Pro 7 Series",
        "payload": {
            "product_query": "Shaw Floorté Pro 7 Series 00100",
            "vendor_name": "Shaw",
            "product_code": "00100",
        },
    },
    # Shaw — query only, no explicit vendor
    {
        "label": "Shaw (inferred from query) - Bellera carpet",
        "payload": {
            "product_query": "Shaw Bellera carpet 5E269",
        },
    },
    # Mohawk — Revwood hardwood
    {
        "label": "Mohawk - RevWood Blackthorn",
        "payload": {
            "product_query": "Mohawk RevWood Blackthorn 7894",
            "vendor_name": "Mohawk",
            "product_code": "7894",
        },
    },
    # Mohawk — query with product name only
    {
        "label": "Mohawk - SolidTech Plus",
        "payload": {
            "product_query": "Mohawk SolidTech Plus vinyl",
        },
    },
    # Daltile — porcelain tile
    {
        "label": "Daltile - Restore Matte tile",
        "payload": {
            "product_query": "Daltile Restore Matte White 12x24 RE01",
            "vendor_name": "Daltile",
            "product_code": "RE01",
        },
    },
    # Altro — Classic 25 Pewter Grey with explicit vendor + code
    {
        "label": "Altro - Classic 25 Pewter Grey",
        "payload": {
            "product_query": "Altro Classic 25 Pewter Grey X2539R11",
            "vendor_name": "Altro",
            "product_code": "X2539R11",
            "color": "Pewter Grey",
        },
    },
    # Altro — inferred from query prefix, no explicit vendor
    {
        "label": "Altro (inferred) - Classic 25",
        "payload": {
            "product_query": "Altro Classic 25 safety flooring",
        },
    },
    # No vendor — test all-vendor fallback search
    {
        "label": "No vendor - ambiguous query",
        "payload": {
            "product_query": "Waterproof luxury vinyl plank 7894",
        },
    },
]


def print_result(label: str, result) -> None:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"{'='*60}")
    print(f"  Success:        {result.success}")
    print(f"  Vendor:         {result.vendor}")
    print(f"  Product:        {result.product_name}")
    print(f"  Code:           {result.product_code}")
    print(f"  Match quality:  {result.match_quality:.2f}  ({result.match_details})")
    print(f"  Product URL:    {result.product_url}")
    print()
    docs = {
        "Warranty":        result.warranty,
        "Spec Sheet":      result.spec_sheet,
        "Care/Maint.":     result.care_maintenance,
        "Installation":    result.installation_guide,
        "Color Ref":       result.color_reference,
    }
    for name, doc in docs.items():
        if doc.downloaded:
            print(f"  [{name:14s}] DOWNLOADED -> {doc.local_path}")
        elif doc.url:
            print(f"  [{name:14s}] URL found (not downloaded): {doc.url}")
        else:
            print(f"  [{name:14s}] NOT FOUND  ({doc.error or 'no URL on page'})")

    if result.warnings:
        print(f"\n  Warnings:")
        for w in result.warnings:
            print(f"    - {w}")
    if result.errors:
        print(f"\n  Errors:")
        for e in result.errors:
            print(f"    ! {e}")
    print()


async def run_tests(vendor_filter: Optional[str] = None, dry_run: bool = False) -> None:
    from typing import Optional
    payloads = TEST_PAYLOADS
    if vendor_filter:
        vf = vendor_filter.lower()
        payloads = [
            p for p in payloads
            if vf in p["label"].lower()
            or vf in p["payload"].get("vendor_name", "").lower()
        ]
        if not payloads:
            print(f"No test payloads matched vendor filter '{vendor_filter}'")
            return

    print(f"\nRunning {len(payloads)} test(s)...\n")

    if dry_run:
        for item in payloads:
            print(f"DRY RUN: {item['label']} -> {item['payload']}")
        return

    pass_count = 0
    fail_count = 0

    for item in payloads:
        label = item["label"]
        payload = WebhookPayload(**item["payload"])
        try:
            result = await retrieve_flooring_pdfs(payload)
            print_result(label, result)
            if result.success:
                pass_count += 1
            else:
                fail_count += 1
        except Exception as exc:
            print(f"\nFATAL ERROR for '{label}': {exc}\n")
            fail_count += 1

    print(f"\n{'='*60}")
    print(f"Results: {pass_count} passed, {fail_count} failed / {len(payloads)} total")
    print(f"{'='*60}\n")


def main() -> None:
    from typing import Optional
    parser = argparse.ArgumentParser(description="Test flooring PDF MCP server against real vendor sites")
    parser.add_argument("--vendor", help="Filter tests by vendor name (e.g. shaw, mohawk, daltile)")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without scraping")
    args = parser.parse_args()

    asyncio.run(run_tests(vendor_filter=args.vendor, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
