"""
tests/test_browse.py

Integration tests for the Browse API:
- GET /sections
- GET /nodes/{id}
- GET /search
- GET /nodes/{id}/diff
"""

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


def test_browse_endpoints(client, db_session: Session):
    """
    Test sections list, node details, search, and diffing features.
    """
    # ── Setup database with 2 versions ──
    with tempfile.TemporaryDirectory() as tmpdir:
        # V1: H1: Safety -> H2: Warnings (body: "Wear gloves.")
        pdf_v1_path = Path(tmpdir) / "v1.pdf"
        items_v1 = [
            {"type": "text", "text": "Safety", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "Warnings", "size": 14.0, "bold": True, "y": 150},
            {"type": "text", "text": "Wear gloves.", "size": 10.0, "bold": False, "y": 170},
        ]
        create_mock_pdf(pdf_v1_path, items_v1)
        client.post(
            "/ingest",
            data={"title": "Manual", "device_model": "CT-200"},
            files={"file": ("v1.pdf", open(pdf_v1_path, "rb"), "application/pdf")},
        )

        # V2: H1: Safety -> H2: Warnings (body: "Wear gloves and mask.") (changed)
        pdf_v2_path = Path(tmpdir) / "v2.pdf"
        items_v2 = [
            {"type": "text", "text": "Safety", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "Warnings", "size": 14.0, "bold": True, "y": 150},
            {"type": "text", "text": "Wear gloves and mask.", "size": 10.0, "bold": False, "y": 170},
        ]
        create_mock_pdf(pdf_v2_path, items_v2)
        client.post(
            "/ingest",
            data={"title": "Manual", "device_model": "CT-200"},
            files={"file": ("v2.pdf", open(pdf_v2_path, "rb"), "application/pdf")},
        )

    # ── 1. Test GET /sections ──
    # Get level 1 sections for V2 (latest)
    resp = client.get("/sections", params={"device_model": "CT-200"})
    assert resp.status_code == 200
    sections = resp.json()
    assert len(sections) == 1
    assert sections[0]["heading"] == "Safety"
    assert sections[0]["level"] == 1

    # Get level 2 sections for V2
    resp_l2 = client.get("/sections", params={"device_model": "CT-200", "level": 2})
    assert resp_l2.status_code == 200
    sections_l2 = resp_l2.json()
    assert len(sections_l2) == 1
    assert sections_l2[0]["heading"] == "Warnings"
    assert sections_l2[0]["level"] == 2

    # ── 2. Test GET /nodes/{id} ──
    safety_node_id = sections[0]["id"]
    resp_node = client.get(f"/nodes/{safety_node_id}")
    assert resp_node.status_code == 200
    node_data = resp_node.json()
    assert node_data["heading"] == "Safety"
    assert len(node_data["children"]) == 1
    assert node_data["children"][0]["heading"] == "Warnings"

    # ── 3. Test GET /search ──
    # Search for "gloves" (should find Warnings node in latest V2 version)
    resp_search = client.get("/search", params={"q": "gloves"})
    assert resp_search.status_code == 200
    results = resp_search.json()
    assert len(results) == 1
    assert results[0]["heading"] == "Warnings"
    assert "Wear gloves and mask." in results[0]["body_text"]

    # Search for "gloves" in version 1 explicitly
    resp_search_v1 = client.get("/search", params={"q": "gloves", "version_number": "1"})
    assert resp_search_v1.status_code == 200
    results_v1 = resp_search_v1.json()
    assert len(results_v1) == 1
    assert results_v1[0]["heading"] == "Warnings"
    assert results_v1[0]["body_text"].strip() == "Wear gloves."

    # ── 4. Test GET /nodes/{id}/diff ──
    # Warnings node ID in V2:
    warnings_node_v2_id = sections_l2[0]["id"]
    resp_diff = client.get(f"/nodes/{warnings_node_v2_id}/diff")
    assert resp_diff.status_code == 200
    diff_data = resp_diff.json()
    
    assert diff_data["has_changes"] is True
    assert diff_data["previous_node_id"] is not None
    
    # Verify line-by-line diff content
    assert "-Wear gloves." in diff_data["diff_text"]
    assert "+Wear gloves and mask." in diff_data["diff_text"]

    # Check history array (should have v1 and v2 entries chronologically)
    history = diff_data["history"]
    assert len(history) == 2
    assert history[0]["version_number"] == 1
    assert history[1]["version_number"] == 2
    assert history[0]["is_changed"] is False
    assert history[1]["is_changed"] is True
