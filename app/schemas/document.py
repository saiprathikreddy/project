"""
app/schemas/document.py

Pydantic schemas for Ingestion request/response payloads.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    version_number: int
    filename: str
    file_hash: str
    description: Optional[str] = None
    ingested_at: datetime


class IngestResponse(BaseModel):
    document_id: int
    version_id: int
    version_number: int
    filename: str
    node_count: int
    message: str
