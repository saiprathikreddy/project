"""
tests/test_generate.py

Integration tests for POST /generations endpoint and LLM store service.
Mocks MongoDB store and Gemini client to run fast, hermetic offline tests.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.sqlite import Base, engine, SessionLocal
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.models.selection import Selection, SelectionNode
from app.models.generation import Generation
from app.schemas.generation import TestCase as ModelTestCase, QATestCaseList
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


@pytest.mark.anyio
@patch("app.services.llm_client.GeminiClientService.generate_qa_test_cases")
@patch("app.services.llm_store.LLMStoreService.save_generation")
@patch("app.services.llm_store.LLMStoreService.get_generation")
@patch("app.services.llm_store.LLMStoreService.init_indexes")
async def test_generate_pipeline(
    mock_init_indexes,
    mock_get_generation,
    mock_save_generation,
    mock_generate_qa,
    client,
    db_session: Session
):
    """
    Verifies that a fresh generation call saves to MongoDB and SQLite (is_cached=False),
    and a subsequent duplicate call retrieves from MongoDB directly (is_cached=True).
    """
    # 1. Setup mock functions
    mock_init_indexes.return_value = None
    mock_save_generation.return_value = "507f1f77bcf86cd799439011"  # Mock MongoDB Object ID
    
    mock_test_cases = [
        ModelTestCase(
            id="TC-001",
            title="Mock test 1",
            description="Desc 1",
            preconditions=["Cond 1"],
            steps=["Step 1"],
            expected_result="Outcome 1"
        )
    ]
    mock_generate_qa.return_value = (
        QATestCaseList(test_cases=mock_test_cases),
        False,  # is_cached returned from LLM service
        "gemini-1.5-flash"
    )
    
    # Setup V1 PDF in SQLite
    doc = Document(title="Manual", device_model="CT-200")
    db_session.add(doc)
    db_session.flush()
    ver = DocumentVersion(document_id=doc.id, version_number=1, filename="v1.pdf", file_hash="hash")
    db_session.add(ver)
    db_session.flush()
    node = Node(
        heading="Safety Warnings",
        level=1,
        body_text="Don't touch electrical parts.",
        node_type="heading",
        content_hash="hash_v1",
        heading_path="Safety Warnings",
        document_version_id=ver.id,
    )
    db_session.add(node)
    db_session.flush()
    
    sel = Selection(name="Safety", document_version_id=ver.id)
    db_session.add(sel)
    db_session.flush()
    sn = SelectionNode(selection_id=sel.id, node_id=node.id, content_hash_at_selection="hash_v1")
    db_session.add(sn)
    db_session.commit()

    # ── 2. Run Fresh Generation (POST /generations) ──
    resp_fresh = client.post(
        "/generations",
        json={"selection_id": sel.id}
    )
    
    assert resp_fresh.status_code == 201
    fresh_data = resp_fresh.json()
    assert fresh_data["selection_id"] == sel.id
    assert fresh_data["is_cached"] is False
    assert fresh_data["model_used"] == "gemini-1.5-flash"
    assert len(fresh_data["test_cases"]) == 1
    assert fresh_data["test_cases"][0]["id"] == "TC-001"
    
    # Check SQLite record was created
    db_gen = db_session.query(Generation).filter(Generation.selection_id == sel.id).first()
    assert db_gen is not None
    assert db_gen.mongo_document_id == "507f1f77bcf86cd799439011"
    assert db_gen.is_cached is False

    # ── 3. Run Cached Generation (POST /generations again) ──
    # Mock LLM service to return cached true
    mock_generate_qa.return_value = (
        QATestCaseList(test_cases=[]),
        True,  # is_cached returned from LLM service
        "gemini-1.5-flash"
    )
    # Mock MongoDB load function
    mock_get_generation.return_value = mock_test_cases

    resp_cached = client.post(
        "/generations",
        json={"selection_id": sel.id}
    )
    
    assert resp_cached.status_code == 201
    cached_data = resp_cached.json()
    assert cached_data["selection_id"] == sel.id
    assert cached_data["is_cached"] is True
    assert len(cached_data["test_cases"]) == 1
    assert cached_data["test_cases"][0]["id"] == "TC-001"
    
    # Verify that get_generation was called to fetch from MongoDB
    mock_get_generation.assert_called_once_with("507f1f77bcf86cd799439011")
