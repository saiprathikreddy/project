"""
app/schemas/selection.py

Pydantic schemas for the Selection API (version-pinned baskets of nodes).
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SelectionCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Unique label for this selection")
    device_model: str = Field("CT-200", description="Device model identifier")
    version_number: Optional[int] = Field(None, description="Version number to pin. Defaults to latest.")
    node_ids: List[int] = Field(..., min_length=1, description="List of node IDs to select")
    description: Optional[str] = Field(None, description="Optional description of the selection purpose")


class SelectionNodeResponse(BaseModel):
    node_id: int
    heading: str
    level: int
    node_type: str
    content_hash_at_selection: str


class SelectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    document_version_id: int
    version_number: int
    device_model: str
    description: Optional[str]
    created_at: datetime
    nodes: List[SelectionNodeResponse]
