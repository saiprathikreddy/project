"""
app/routers/selection.py

FastAPI router for Selection API.
Allows users to submit a named, version-pinned set of node IDs, snapshotting
their content hashes at creation time to support future staleness checks.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.sqlite import get_db
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.models.selection import Selection, SelectionNode
from app.schemas.selection import SelectionCreate, SelectionNodeResponse, SelectionResponse

router = APIRouter()


@router.post("", response_model=SelectionResponse, status_code=201)
def create_selection(
    payload: SelectionCreate,
    db: Session = Depends(get_db),
):
    """
    Submits a named set of node IDs, version-pinned to the exact text at time of creation.
    Stores a snapshot of each node's content hash to detect staleness later.
    """
    # 1. Resolve Document & DocumentVersion
    doc = db.query(Document).filter(Document.device_model == payload.device_model).first()
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Document model '{payload.device_model}' not found."
        )

    if payload.version_number is None:
        if not doc.versions:
            raise HTTPException(
                status_code=404,
                detail=f"No ingested versions found for model '{payload.device_model}'."
            )
        db_version = max(doc.versions, key=lambda v: v.version_number)
    else:
        db_version = (
            db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number == payload.version_number
            )
            .first()
        )
        if not db_version:
            raise HTTPException(
                status_code=404,
                detail=f"Version {payload.version_number} of model '{payload.device_model}' not found."
            )

    # 2. Validate all node_ids exist and belong to this version
    nodes = (
        db.query(Node)
        .filter(
            Node.id.in_(payload.node_ids),
            Node.document_version_id == db_version.id
        )
        .all()
    )

    found_node_ids = {n.id for n in nodes}
    missing_ids = set(payload.node_ids) - found_node_ids
    if missing_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid nodes for version {db_version.version_number}. "
                f"IDs not found in this version: {list(missing_ids)}."
            )
        )

    # 3. Create the Selection
    selection = Selection(
        name=payload.name,
        document_version_id=db_version.id,
        description=payload.description,
    )
    db.add(selection)
    db.flush()  # Populates selection.id

    # 4. Create SelectionNode entries (snapshotting content_hash)
    selection_nodes = []
    node_responses = []
    
    # Preserve order of node_ids as requested in payload
    nodes_by_id = {n.id: n for n in nodes}
    for n_id in payload.node_ids:
        node = nodes_by_id[n_id]
        sel_node = SelectionNode(
            selection_id=selection.id,
            node_id=node.id,
            content_hash_at_selection=node.content_hash,
        )
        db.add(sel_node)
        selection_nodes.append(sel_node)
        
        node_responses.append(
            SelectionNodeResponse(
                node_id=node.id,
                heading=node.heading,
                level=node.level,
                node_type=node.node_type,
                content_hash_at_selection=node.content_hash,
            )
        )

    db.commit()

    return SelectionResponse(
        id=selection.id,
        name=selection.name,
        document_version_id=selection.document_version_id,
        version_number=db_version.version_number,
        device_model=doc.device_model,
        description=selection.description,
        created_at=selection.created_at,
        nodes=node_responses,
    )


@router.get("/{id}", response_model=SelectionResponse)
def get_selection_by_id(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Retrieves a selection by its ID along with its associated version-pinned nodes.
    """
    selection = db.query(Selection).filter(Selection.id == id).first()
    if not selection:
        raise HTTPException(status_code=404, detail=f"Selection with ID {id} not found.")

    node_responses = []
    for sel_node in selection.selection_nodes:
        node_responses.append(
            SelectionNodeResponse(
                node_id=sel_node.node_id,
                heading=sel_node.node.heading,
                level=sel_node.node.level,
                node_type=sel_node.node.node_type,
                content_hash_at_selection=sel_node.content_hash_at_selection,
            )
        )

    return SelectionResponse(
        id=selection.id,
        name=selection.name,
        document_version_id=selection.document_version_id,
        version_number=selection.document_version.version_number,
        device_model=selection.document_version.document.device_model,
        description=selection.description,
        created_at=selection.created_at,
        nodes=node_responses,
    )
