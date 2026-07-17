"""
app/schemas/node.py

Pydantic schemas for browsing and searching document nodes.
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_version_id: int
    parent_id: Optional[int]
    order_index: int
    heading: str
    level: int
    body_text: str
    node_type: str
    content_hash: str
    heading_path: str
    is_changed: bool
    previous_version_node_id: Optional[int]


class NodeWithChildrenResponse(NodeResponse):
    children: List[NodeResponse] = []


class SectionListResponse(BaseModel):
    nodes: List[NodeResponse]


class DiffHistoryItem(BaseModel):
    node_id: int
    version_id: int
    version_number: int
    heading: str
    content_hash: str
    is_changed: bool


class NodeDiffResponse(BaseModel):
    node_id: int
    previous_node_id: Optional[int] = None
    has_changes: bool
    diff_text: str
    history: List[DiffHistoryItem]
