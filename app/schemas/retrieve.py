"""
app/schemas/retrieve.py

Pydantic schemas for the Retrieval API, including staleness reports.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.generation import TestCase


class NodeStalenessDetail(BaseModel):
    node_id: int
    heading_path: str
    status: str = Field(..., description="fresh | changed | deleted")
    latest_node_id: Optional[int] = None


class StalenessReportResponse(BaseModel):
    is_stale: bool = Field(..., description="True if any node in the selection is changed or deleted in the latest version")
    pinned_version: int = Field(..., description="The document version number pinned by the selection")
    latest_version: int = Field(..., description="The latest ingested document version number")
    details: List[NodeStalenessDetail]


class GenerationWithStalenessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    selection_id: int
    is_cached: bool
    model_used: str
    generated_at: datetime
    test_cases: List[TestCase]
    staleness: StalenessReportResponse
