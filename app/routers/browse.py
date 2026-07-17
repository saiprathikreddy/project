"""
app/routers/browse.py

FastAPI router for browsing, searching, and diffing document nodes in SQLite.
"""

import difflib
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.sqlite import get_db
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.schemas.node import DiffHistoryItem, NodeDiffResponse, NodeResponse, NodeWithChildrenResponse

router = APIRouter()


def _get_latest_version_id(db: Session, device_model: str) -> int:
    """Helper to resolve the latest database version ID for a device model."""
    doc = db.query(Document).filter(Document.device_model == device_model).first()
    if not doc or not doc.versions:
        raise HTTPException(
            status_code=404,
            detail=f"No ingested document versions found for model '{device_model}'."
        )
    latest_ver = max(doc.versions, key=lambda v: v.version_number)
    return latest_ver.id


@router.get("/sections", response_model=List[NodeResponse])
def get_sections(
    device_model: str = Query("CT-200", description="Device model identifier"),
    version_number: Optional[int] = Query(None, description="Document version number. Defaults to latest."),
    level: Optional[int] = Query(1, description="Heading depth level to filter by (e.g. 1=H1, 2=H2)"),
    db: Session = Depends(get_db),
):
    """
    Lists sections for a document version.
    Defaults to level 1 (top-level H1 headings) of the latest version of the CT-200 manual.
    """
    # 1. Resolve version_id
    if version_number is None:
        version_id = _get_latest_version_id(db, device_model)
    else:
        doc = db.query(Document).filter(Document.device_model == device_model).first()
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document '{device_model}' not found.")
        ver = (
            db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number == version_number
            )
            .first()
        )
        if not ver:
            raise HTTPException(
                status_code=404,
                detail=f"Version {version_number} of model '{device_model}' not found."
            )
        version_id = ver.id

    # 2. Query nodes
    query = db.query(Node).filter(Node.document_version_id == version_id)
    if level is not None:
        query = query.filter(Node.level == level)

    nodes = query.order_by(Node.order_index).all()
    return nodes


@router.get("/nodes/{id}", response_model=NodeWithChildrenResponse)
def get_node_by_id(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Fetches a specific node by its ID along with all its immediate children.
    """
    node = db.query(Node).filter(Node.id == id).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node with ID {id} not found.")
    return node


@router.get("/search", response_model=List[NodeResponse])
def search_text(
    q: str = Query(..., min_length=1, description="Search query string"),
    device_model: str = Query("CT-200", description="Device model identifier"),
    version_number: Optional[str] = Query(
        None,
        description="Version number to search (e.g. '1', '2'). Defaults to latest. Pass 'all' to search all versions."
    ),
    node_type: Optional[str] = Query(None, description="Filter by node type: 'heading', 'table', 'figure', 'caption'"),
    db: Session = Depends(get_db),
):
    """
    Search text inside sections (searches both the heading and body_text).
    Can filter by version and node_type.
    """
    query = db.query(Node)

    # Apply version filter
    if version_number != "all":
        if version_number is None:
            version_id = _get_latest_version_id(db, device_model)
        else:
            try:
                v_num = int(version_number)
            except ValueError:
                raise HTTPException(status_code=400, detail="version_number must be an integer or 'all'.")
            
            doc = db.query(Document).filter(Document.device_model == device_model).first()
            if not doc:
                raise HTTPException(status_code=404, detail=f"Document '{device_model}' not found.")
            ver = (
                db.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.version_number == v_num
                )
                .first()
            )
            if not ver:
                raise HTTPException(status_code=404, detail=f"Version {v_num} not found.")
            version_id = ver.id
        
        query = query.filter(Node.document_version_id == version_id)

    # Apply node type filter
    if node_type:
        query = query.filter(Node.node_type == node_type)

    # Search query filter (case-insensitive LIKE search on heading or body_text)
    search_filter = f"%{q}%"
    query = query.filter(
        (Node.heading.like(search_filter)) | (Node.body_text.like(search_filter))
    )

    results = query.order_by(Node.id).all()
    return results


@router.get("/nodes/{id}/diff", response_model=NodeDiffResponse)
def get_node_diff_and_history(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Calculates the text diff between this node and its previous version counterpart.
    Also returns a chronological version history (audit trail) of the node path.
    """
    # 1. Fetch current node
    node = db.query(Node).filter(Node.id == id).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node with ID {id} not found.")

    # 2. Compute unified text diff against immediate previous version (if it exists)
    diff_text = ""
    has_changes = False
    prev_node = node.previous_version_node
    
    if prev_node:
        has_changes = node.is_changed
        
        # Calculate line-by-line unified diff
        diff_lines = list(
            difflib.unified_diff(
                prev_node.body_text.splitlines(),
                node.body_text.splitlines(),
                fromfile=f"v{prev_node.document_version.version_number} - {prev_node.heading}",
                tofile=f"v{node.document_version.version_number} - {node.heading}",
                lineterm="",
            )
        )
        diff_text = "\n".join(diff_lines)
        if not diff_text and has_changes:
            # If the header changed but body text is identical
            diff_text = f"Heading renamed:\n- {prev_node.heading}\n+ {node.heading}"
    else:
        diff_text = "No previous version available (this is the first version this node appeared)."

    # 3. Build chronological audit trail history
    history: List[DiffHistoryItem] = []
    
    # We walk the history backward using previous_version_node relation
    curr = node
    while curr is not None:
        history.append(
            DiffHistoryItem(
                node_id=curr.id,
                version_id=curr.document_version_id,
                version_number=curr.document_version.version_number,
                heading=curr.heading,
                content_hash=curr.content_hash,
                is_changed=curr.is_changed,
            )
        )
        curr = curr.previous_version_node

    # Reverse to make it chronological (oldest to newest)
    history.reverse()

    return NodeDiffResponse(
        node_id=node.id,
        previous_node_id=prev_node.id if prev_node else None,
        has_changes=has_changes,
        diff_text=diff_text,
        history=history,
    )
