"""
tests/test_ingest.py

Integration tests for POST /ingest endpoint.
Tests:
1. Initial PDF upload (creates Document, DocumentVersion 1, and Node tree in SQLite).
2. Duplicate upload of the identical PDF (returns 400 Bad Request).
3. Upload of a new version PDF (creates DocumentVersion 2, links matched nodes, and marks updates).
"""

import io
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.sqlite import Base, engine, SessionLocal
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from tests.test_parser import create_mock_pdf


@pytest.fixture(name="db_session")
def fixture_db_session():
    """Drops and recreates SQLite database tables for isolation between tests."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="client")
def fixture_client():
    return TestClient(app)


def test_ingest_pipeline(client, db_session: Session):
    """
    Full workflow verification:
    1. Upload V1 manual -> verify created models.
    2. Upload V1 again -> verify 400 error.
    3. Upload V2 manual (some identical nodes, some changed/new nodes) -> verify matching.
    """
    # ── 1. Create V1 PDF ──
    # Content:
    #   H1: Overview -> body: "This device is a blood pressure monitor."
    #   H1: Setup -> body: "Insert two AA batteries."
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_v1_path = Path(tmpdir) / "ct200_v1.pdf"
        items_v1 = [
            {"type": "text", "text": "Overview", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "This device is a blood pressure monitor.", "size": 10.0, "bold": False, "y": 120},
            {"type": "text", "text": "Setup", "size": 18.0, "bold": True, "y": 200},
            {"type": "text", "text": "Insert two AA batteries.", "size": 10.0, "bold": False, "y": 220},
        ]
        create_mock_pdf(pdf_v1_path, items_v1)

        # Upload V1
        with open(pdf_v1_path, "rb") as f:
            response = client.post(
                "/ingest",
                data={
                    "title": "CT-200 Blood Pressure Monitor Manual",
                    "device_model": "CT-200",
                    "description": "V1 manual draft",
                },
                files={"file": ("ct200_v1.pdf", f, "application/pdf")},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["version_number"] == 1
        assert data["node_count"] == 2
        assert "Successfully ingested" in data["message"]

        # Check DB V1 contents
        doc = db_session.query(Document).filter(Document.device_model == "CT-200").first()
        assert doc is not None
        assert doc.title == "CT-200 Blood Pressure Monitor Manual"
        assert len(doc.versions) == 1
        
        v1_nodes = db_session.query(Node).filter(Node.document_version_id == doc.versions[0].id).all()
        assert len(v1_nodes) == 2
        
        # Verify parent pointers are None for H1 root elements
        for n in v1_nodes:
            assert n.parent_id is None
            assert n.previous_version_node_id is None
            assert n.is_changed is False

        # ── 2. Duplicate upload test ──
        # Upload identical PDF file again
        with open(pdf_v1_path, "rb") as f:
            response_dup = client.post(
                "/ingest",
                data={
                    "title": "CT-200 Blood Pressure Monitor Manual",
                    "device_model": "CT-200",
                    "description": "Duplicate upload check",
                },
                files={"file": ("ct200_v1.pdf", f, "application/pdf")},
            )
        assert response_dup.status_code == 400
        assert "already been ingested" in response_dup.json()["detail"]

    # ── 3. Create and upload V2 PDF ──
    # Content changes:
    #   H1: Overview -> body identical: "This device is a blood pressure monitor." (unchanged)
    #   H1: Setup -> body changed: "Insert four AAA batteries instead." (changed)
    #   H1: Maintenance -> body new: "Keep device dry." (new)
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_v2_path = Path(tmpdir) / "ct200_v2.pdf"
        items_v2 = [
            {"type": "text", "text": "Overview", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "This device is a blood pressure monitor.", "size": 10.0, "bold": False, "y": 120},
            {"type": "text", "text": "Setup", "size": 18.0, "bold": True, "y": 200},
            {"type": "text", "text": "Insert four AAA batteries instead.", "size": 10.0, "bold": False, "y": 220},
            {"type": "text", "text": "Maintenance", "size": 18.0, "bold": True, "y": 300},
            {"type": "text", "text": "Keep device dry.", "size": 10.0, "bold": False, "y": 320},
        ]
        create_mock_pdf(pdf_v2_path, items_v2)

        # Upload V2
        with open(pdf_v2_path, "rb") as f:
            response_v2 = client.post(
                "/ingest",
                data={
                    "title": "CT-200 Blood Pressure Monitor Manual",
                    "device_model": "CT-200",
                    "description": "V2 manual draft",
                },
                files={"file": ("ct200_v2.pdf", f, "application/pdf")},
            )

        assert response_v2.status_code == 201
        data_v2 = response_v2.json()
        assert data_v2["version_number"] == 2
        assert data_v2["node_count"] == 3

        # Check DB V2 contents
        v2_version = db_session.query(DocumentVersion).filter(DocumentVersion.version_number == 2).first()
        assert v2_version is not None
        
        # Verify matching logic on V2 nodes
        v2_nodes = db_session.query(Node).filter(Node.document_version_id == v2_version.id).all()
        assert len(v2_nodes) == 3

        # Map by heading to verify individual states
        v2_map = {n.heading: n for n in v2_nodes}

        # Node 1: Overview (Unchanged)
        overview_node = v2_map["Overview"]
        assert overview_node.previous_version_node_id is not None
        assert overview_node.is_changed is False

        # Node 2: Setup (Changed)
        setup_node = v2_map["Setup"]
        assert setup_node.previous_version_node_id is not None
        assert setup_node.is_changed is True

        # Node 3: Maintenance (New)
        maint_node = v2_map["Maintenance"]
        assert maint_node.previous_version_node_id is None
        assert maint_node.is_changed is False
