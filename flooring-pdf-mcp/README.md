# Flooring PDF MCP Server

Retrieves manufacturer PDF documentation (warranty, spec sheet, care/maintenance, installation guide, color reference) for flooring products from Shaw, Mohawk, Daltile, Armstrong, and Mannington.

Exposes two interfaces:
- **HTTP webhook** at `POST /webhook` for CRM integration
- **MCP stdio server** for AI assistant (Claude) integration

---

## Quick Start

### 1. Install dependencies

```bash
cd flooring-pdf-mcp
pip install -r requirements.txt
playwright install chromium
```

### 2. Run the webhook server

```bash
python main.py
# → Listening on http://localhost:5000
```

### 3. Test the webhook

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "product_query": "Shaw Floorté Pro 7 Series 00100",
    "vendor_name": "Shaw",
    "product_code": "00100"
  }'
```

### 4. Health check

```bash
curl http://localhost:5000/health
```

---

## Webhook API

### `POST /webhook`

**Request body** (`WebhookPayload`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `product_query` | string | **Yes** | Product name + code, e.g. `"Shaw 00134"` or `"Mohawk Revwood Blackthorn 7894"` |
| `vendor_name` | string | No | Explicit vendor: Shaw, Mohawk, Daltile, Armstrong, Mannington |
| `product_sku` | string | No | Product SKU |
| `product_code` | string | No | Product code |
| `color` | string | No | Color name or code |

**Response** (`PDFResponse`):

```json
{
  "success": true,
  "vendor": "Shaw",
  "product_name": "Floorté Pro 7 Series",
  "product_code": "00100",
  "product_url": "https://www.shawfloors.com/flooring/hardwood/...",
  "match_quality": 0.87,
  "match_details": "name similarity 72%, product code matched",
  "warranty": {
    "url": "https://cdn.shawfloors.com/.../warranty.pdf",
    "local_path": "/app/downloads/20240115_143022_shaw_00100_warranty.pdf",
    "filename": "20240115_143022_shaw_00100_warranty.pdf",
    "downloaded": true,
    "error": null
  },
  "spec_sheet": { ... },
  "care_maintenance": { ... },
  "installation_guide": { ... },
  "color_reference": { "url": null, "downloaded": false, "error": "Not found on product page" },
  "timestamp": "2024-01-15T14:30:22Z",
  "errors": [],
  "warnings": ["color_reference: not found on product page"]
}
```

### `match_quality` scoring

| Score | Meaning |
|-------|---------|
| 0.90–1.0 | High confidence — exact SKU/code + name match |
| 0.70–0.89 | Good match — strong name similarity + code match |
| 0.50–0.69 | Moderate — name matched, code not confirmed |
| 0.30–0.49 | Weak — fuzzy name match only |
| 0.0–0.29 | Low — best guess, review manually |

---

## Test Mode

Runs hardcoded payloads against real vendor sites without a running CRM:

```bash
# All test payloads
python -m tests.test_mode

# Filter by vendor
python -m tests.test_mode --vendor shaw
python -m tests.test_mode --vendor mohawk
python -m tests.test_mode --vendor daltile

# Dry run (print payloads, no scraping)
python -m tests.test_mode --dry-run
```

---

## MCP Integration (Claude / AI Assistants)

Run as an MCP stdio server:

```bash
python main.py --mcp
```

Add to your Claude Code `settings.json`:

```json
{
  "mcpServers": {
    "flooring-pdf": {
      "command": "python",
      "args": ["/path/to/flooring-pdf-mcp/main.py", "--mcp"],
      "cwd": "/path/to/flooring-pdf-mcp"
    }
  }
}
```

Available MCP tools:
- `get_flooring_pdfs` — retrieve PDFs for a product (same parameters as webhook)
- `list_supported_vendors` — list configured vendors

---

## Docker

### Build and run

```bash
docker compose up --build
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | HTTP server port |
| `HOST` | `0.0.0.0` | Bind address |
| `DOWNLOADS_DIR` | `/app/downloads` | PDF storage directory |

### PDF persistence

Downloaded PDFs are stored in `./downloads/` (mounted as a volume). Filename format:

```
{YYYYMMDD_HHMMSS}_{vendor}_{product_code}_{doc_type}.pdf
```

Example: `20240115_143022_shaw_00100_warranty.pdf`

---

## Adding New Vendors

1. Add vendor config to `config/vendors.yaml` following the existing pattern
2. Create `src/vendors/{vendor_id}.py` extending `BaseVendor`
3. Register in `src/vendors/__init__.py` and `src/core.py` `VENDOR_CLASSES`
4. Add test payloads to `tests/test_mode.py`

The `BaseVendor.extract_pdf_links_from_page()` generic extractor works for most sites. Override `get_pdf_links()` only for unusual page structures.

---

## Notes on Scraping

- Uses Playwright (Chromium headless) for JavaScript-heavy pages
- Rate limiting: 2–2.5 seconds between requests per vendor (configurable in `vendors.yaml`)
- Retry logic: 3 attempts with exponential backoff (2s, 4s, 8s) for failed downloads
- Images/fonts/media are blocked to speed up page loads
- Vendor websites change layouts; if scraping breaks, update the selectors in the relevant `src/vendors/{vendor}.py`
