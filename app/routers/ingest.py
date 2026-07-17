"""
app/routers/ingest.py

FastAPI router for PDF document ingestion.
Parses medical device manuals, checks for duplicate file hashes,
persists the node tree, and matches nodes against the previous version.
"""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.sqlite import get_db
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.schemas.document import IngestResponse
from app.services.parser import PDFParser, ParsedNode
from app.services.versioner import DocumentVersioner

router = APIRouter()


def _flatten_db_nodes(node: Node, nodes_list: List[Node]) -> None:
    """Helper to recursively flatten the SQLAlchemy Node tree into a flat list."""
    nodes_list.append(node)
    for child in node.children:
        _flatten_db_nodes(child, nodes_list)


def _convert_parsed_to_db_node(p_node: ParsedNode, version_id: int) -> Node:
    """Recursively converts ParsedNode tree to SQLAlchemy Node tree."""
    db_node = Node(
        heading=p_node.heading,
        level=p_node.level,
        body_text=p_node.body_text,
        node_type=p_node.node_type,
        order_index=p_node.order_index,
        content_hash=p_node.content_hash,
        heading_path=p_node.heading_path,
        document_version_id=version_id,
    )
    for p_child in p_node.children:
        db_child = _convert_parsed_to_db_node(p_child, version_id)
        db_node.children.append(db_child)
    return db_node


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_pdf(
    title: str = Form(..., description="Document title, e.g. 'CT-200 Blood Pressure Monitor Manual'"),
    device_model: str = Form(..., description="Device model identifier, e.g. 'CT-200'"),
    description: Optional[str] = Form(None, description="Optional version notes"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Ingests a medical device PDF manual:
    1. Computes raw file hash to prevent duplicate uploads.
    2. Finds or creates the parent Document.
    3. Monotonically increments version number.
    4. Parses PDF into a hierarchical node tree.
    5. Persists the tree and links nodes to the previous version to track changes.
    """
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # 1. Compute file hash to prevent exact duplicate uploads
    file_bytes = await file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    await file.seek(0)

    # 2. Find or create Document
    doc = db.query(Document).filter(Document.device_model == device_model).first()
    if not doc:
        doc = Document(title=title, device_model=device_model)
        db.add(doc)
        db.flush()  # Populates doc.id

    # 3. Check for duplicate version upload
    existing_ver = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.file_hash == file_hash
        )
        .first()
    )
    if existing_ver:
        raise HTTPException(
            status_code=400,
            detail=f"This exact PDF file has already been ingested as version {existing_ver.version_number}."
        )

    # Calculate next version number
    version_numbers = [v.version_number for v in doc.versions]
    next_version = max(version_numbers) + 1 if version_numbers else 1

    # Get the previous version ID (if any) to match against
    previous_version_id = None
    if doc.versions:
        # Pinned to the highest version number
        latest_ver_node = max(doc.versions, key=lambda v: v.version_number)
        previous_version_id = latest_ver_node.id

    # 4. Save uploaded file to temp path to parse with PyMuPDF
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(file_bytes)
        tmp_file_path = Path(tmp_file.name)

    try:
        parser = PDFParser(tmp_file_path)
        root_parsed = parser.parse()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {str(e)}")
    finally:
        # Clean up temp file
        if tmp_file_path.exists():
            os.remove(tmp_file_path)

    # 5. Create new DocumentVersion
    db_version = DocumentVersion(
        document_id=doc.id,
        version_number=next_version,
        filename=file.filename,
        file_hash=file_hash,
        description=description,
    )
    db.add(db_version)
    db.flush()  # Populates db_version.id

    # Convert parsed tree to DB Node tree (skip synthetic Root node children)
    db_root_nodes: List[Node] = []
    for parsed_child in root_parsed.children:
        db_node = _convert_parsed_to_db_node(parsed_child, db_version.id)
        db_root_nodes.append(db_node)
        db.add(db_node)

    db.flush()  # Generates DB IDs for the entire tree

    # Flatten all newly created DB nodes for matching
    new_flat_nodes: List[Node] = []
    for root_node in db_root_nodes:
        _flatten_db_nodes(root_node, new_flat_nodes)

    # 6. Version matching against previous version
    versioner = DocumentVersioner(db)
    versioner.match_and_link_nodes(new_flat_nodes, previous_version_id)

    db.commit()

    return IngestResponse(
        document_id=doc.id,
        version_id=db_version.id,
        version_number=next_version,
        filename=file.filename,
        node_count=len(new_flat_nodes),
        message=f"Successfully ingested {file.filename} as version {next_version}.",
    )
