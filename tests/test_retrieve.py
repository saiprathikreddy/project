"""
tests/test_retrieve.py

Integration tests for the Retrieval API:
- GET /generations/{id}
- GET /generations/nodes/{node_id}
Mocks MongoDB store and Gemini client to run fast, hermetic offline tests.
"""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.sqlite import Base, engine, SessionLocal
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.models.selection import Selection, SelectionNode
from app.models.generation import Generation
from app.schemas.generation import TestCase as ModelTestCase


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


@pytest.mark.anyio
@patch("app.services.llm_store.LLMStoreService.get_generation")
async def test_retrieval_endpoints(
    mock_get_generation,
    client,
    db_session: Session
):
    """
    Verifies that retrieval returns the generation, correct MongoDB payload,
    and active staleness report.
    """
    # ── Setup V1 ──
    doc = Document(title="Manual", device_model="CT-200")
    db_session.add(doc)
    db_session.flush()
    
    v1 = DocumentVersion(document_id=doc.id, version_number=1, filename="v1.pdf", file_hash="hash_v1")
    db_session.add(v1)
    db_session.flush()
    
    node = Node(
        heading="Safety Warnings",
        level=1,
        body_text="Don't touch electrical parts.",
        node_type="heading",
        content_hash="hash_v1",
        heading_path="Safety Warnings",
        document_version_id=v1.id,
    )
    db_session.add(node)
    db_session.flush()

    sel = Selection(name="Safety Basket", document_version_id=v1.id)
    db_session.add(sel)
    db_session.flush()
    
    sn = SelectionNode(selection_id=sel.id, node_id=node.id, content_hash_at_selection="hash_v1")
    db_session.add(sn)
    db_session.flush()

    # Create Generation row
    gen = Generation(
        selection_id=sel.id,
        mongo_document_id="507f1f77bcf86cd799439011",
        node_hashes_snapshot="hash_v1",
        is_cached=False,
        model_used="gemini-1.5-flash",
    )
    db_session.add(gen)
    db_session.commit()

    # Configure mock test cases from MongoDB
    mock_test_cases = [
        ModelTestCase(
            id="TC-001",
            title="Verify grounding warnings",
            description="Details",
            preconditions=["Grounding active"],
            steps=["Touch casing"],
            expected_result="No shock"
        )
    ]
    mock_get_generation.return_value = mock_test_cases

    # ── 1. Test GET /generations/{id} (Fresh/Not Stale since V1 is latest) ──
    resp_get = client.get(f"/generations/{gen.id}")
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["id"] == gen.id
    assert data["selection_id"] == sel.id
    assert len(data["test_cases"]) == 1
    assert data["test_cases"][0]["id"] == "TC-001"
    
    # Verify staleness says False (V1 is latest)
    staleness = data["staleness"]
    assert staleness["is_stale"] is False
    assert staleness["pinned_version"] == 1
    assert staleness["latest_version"] == 1
    assert len(staleness["details"]) == 1
    assert staleness["details"][0]["node_id"] == node.id
    assert staleness["details"][0]["status"] == "fresh"

    # ── Setup V2 in DB (Modifies the node to make it stale) ──
    v2 = DocumentVersion(document_id=doc.id, version_number=2, filename="v2.pdf", file_hash="hash_v2")
    db_session.add(v2)
    db_session.flush()
    
    node_v2 = Node(
        heading="Safety Warnings",
        level=1,
        body_text="Don't touch electrical parts with wet hands.", # changed body
        node_type="heading",
        content_hash="hash_v2_changed",
        heading_path="Safety Warnings",
        document_version_id=v2.id,
    )
    db_session.add(node_v2)
    db_session.commit()

    # ── 2. Test GET /generations/{id} again (Should be stale now) ──
    db_session.refresh(gen)
    resp_stale = client.get(f"/generations/{gen.id}")
    assert resp_stale.status_code == 200
    data_stale = resp_stale.json()
    
    # Verify staleness says True (V2 has a changed hash)
    staleness_stale = data_stale["staleness"]
    assert staleness_stale["is_stale"] is True
    assert staleness_stale["pinned_version"] == 1
    assert staleness_stale["latest_version"] == 2
    assert staleness_stale["details"][0]["status"] == "changed"
    assert staleness_stale["details"][0]["latest_node_id"] == node_v2.id

    # ── 3. Test GET /generations/nodes/{node_id} ──
    resp_node = client.get(f"/generations/nodes/{node.id}")
    assert resp_node.status_code == 200
    node_gens = resp_node.json()
    assert len(node_gens) == 1
    assert node_gens[0]["id"] == gen.id
    assert node_gens[0]["staleness"]["is_stale"] is True
