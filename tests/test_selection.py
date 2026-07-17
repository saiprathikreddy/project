"""
tests/test_selection.py

Integration tests for the Selection API:
- POST /selections
- GET /selections/{id}
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
from app.models.selection import Selection, SelectionNode
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


def test_selection_endpoints(client, db_session: Session):
    """
    Test selection creation, verification of pinned version, validation, and retrieval.
    """
    # ── Setup database with 1 version ──
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "v1.pdf"
        items = [
            {"type": "text", "text": "Safety Warnings", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "Don't touch electrical parts with wet hands.", "size": 10.0, "bold": False, "y": 120},
            {"type": "text", "text": "Storage", "size": 18.0, "bold": True, "y": 200},
            {"type": "text", "text": "Keep in a cool dry place.", "size": 10.0, "bold": False, "y": 220},
        ]
        create_mock_pdf(pdf_path, items)
        client.post(
            "/ingest",
            data={"title": "Manual", "device_model": "CT-200"},
            files={"file": ("v1.pdf", open(pdf_path, "rb"), "application/pdf")},
        )

    # Fetch nodes from DB to get real IDs
    db_nodes = db_session.query(Node).all()
    assert len(db_nodes) == 2
    safety_id = next(n.id for n in db_nodes if "Safety" in n.heading)
    storage_id = next(n.id for n in db_nodes if "Storage" in n.heading)

    # ── 1. Create selection successfully ──
    resp_create = client.post(
        "/selections",
        json={
            "name": "Safety and Storage Pinned",
            "device_model": "CT-200",
            "node_ids": [safety_id, storage_id],
            "description": "Selected key procedures for test generation",
        },
    )
    assert resp_create.status_code == 201
    sel_data = resp_create.json()
    assert sel_data["name"] == "Safety and Storage Pinned"
    assert sel_data["device_model"] == "CT-200"
    assert sel_data["version_number"] == 1
    assert len(sel_data["nodes"]) == 2

    # Check that snapshot hashes are returned
    nodes_res = {n["node_id"]: n for n in sel_data["nodes"]}
    assert nodes_res[safety_id]["content_hash_at_selection"] == db_session.get(Node, safety_id).content_hash
    assert nodes_res[storage_id]["content_hash_at_selection"] == db_session.get(Node, storage_id).content_hash

    # Check SQLite contents
    db_sel = db_session.query(Selection).filter(Selection.id == sel_data["id"]).first()
    assert db_sel is not None
    assert len(db_sel.selection_nodes) == 2

    # ── 2. Validate selection fails with invalid node ID ──
    resp_fail = client.post(
        "/selections",
        json={
            "name": "Invalid Selection",
            "device_model": "CT-200",
            # Include an ID that doesn't exist in the DB (9999)
            "node_ids": [safety_id, 9999],
        },
    )
    assert resp_fail.status_code == 400
    assert "Invalid nodes" in resp_fail.json()["detail"]

    # ── 3. Retrieve selection GET /selections/{id} ──
    sel_id = sel_data["id"]
    resp_get = client.get(f"/selections/{sel_id}")
    assert resp_get.status_code == 200
    get_data = resp_get.json()
    assert get_data["id"] == sel_id
    assert get_data["name"] == "Safety and Storage Pinned"
    assert len(get_data["nodes"]) == 2
    assert get_data["nodes"][0]["node_id"] == safety_id
