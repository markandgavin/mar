from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class WebhookPayload(BaseModel):
    product_query: str = Field(..., description="Product name and/or code, e.g. 'Shaw 00134' or 'Mohawk Revwood Blackthorn 7894'")
    vendor_name: Optional[str] = Field(None, description="Vendor name if known")
    product_sku: Optional[str] = Field(None, description="Product SKU")
    product_code: Optional[str] = Field(None, description="Product code")
    color: Optional[str] = Field(None, description="Color name or code")


class DocumentResult(BaseModel):
    url: Optional[str] = None
    local_path: Optional[str] = None
    filename: Optional[str] = None
    downloaded: bool = False
    error: Optional[str] = None

    def is_found(self) -> bool:
        return self.url is not None or self.local_path is not None


class PDFResponse(BaseModel):
    success: bool = False
    vendor: Optional[str] = None
    product_name: Optional[str] = None
    product_code: Optional[str] = None
    product_url: Optional[str] = None
    match_quality: float = Field(0.0, ge=0.0, le=1.0, description="0.0=no match, 1.0=perfect match")
    match_details: str = ""
    warranty: DocumentResult = Field(default_factory=DocumentResult)
    spec_sheet: DocumentResult = Field(default_factory=DocumentResult)
    care_maintenance: DocumentResult = Field(default_factory=DocumentResult)
    installation_guide: DocumentResult = Field(default_factory=DocumentResult)
    color_reference: DocumentResult = Field(default_factory=DocumentResult)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def docs_found(self) -> int:
        return sum(
            1 for d in [self.warranty, self.spec_sheet, self.care_maintenance,
                        self.installation_guide, self.color_reference]
            if d.is_found()
        )


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    vendors_loaded: int = 0
    downloads_dir: Optional[str] = None
