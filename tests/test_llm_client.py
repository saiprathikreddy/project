"""
tests/test_llm_client.py

Unit tests for GeminiClientService:
- Verify prompt reconstruction
- Verify retry logic on API errors
- Verify fallback behavior when retries are exhausted
- Verify deduplication checks
"""

import datetime
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.db.sqlite import Base, engine, SessionLocal
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.models.selection import Selection, SelectionNode
from app.models.generation import Generation
from app.services.llm_client import GeminiClientService


@pytest.fixture(name="db_session")
def fixture_db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_prompt_reconstruction(db_session: Session):
    """Verifies that selected nodes are correctly concatenated into the prompt."""
    doc = Document(title="Manual", device_model="CT-200")
    db_session.add(doc)
    db_session.flush()

    ver = DocumentVersion(document_id=doc.id, version_number=1, filename="file.pdf", file_hash="abc")
    db_session.add(ver)
    db_session.flush()

    node1 = Node(
        heading="Safety Warnings",
        level=1,
        body_text="Don't touch electrical parts.",
        node_type="heading",
        content_hash="h1",
        heading_path="Safety Warnings",
        document_version_id=ver.id,
    )
    node2 = Node(
        heading="Technical Specifications",
        level=1,
        body_text="Weight: 250g.",
        node_type="heading",
        content_hash="h2",
        heading_path="Technical Specifications",
        document_version_id=ver.id,
    )
    db_session.add_all([node1, node2])
    db_session.flush()

    sel = Selection(name="Test Basket", document_version_id=ver.id)
    db_session.add(sel)
    db_session.flush()

    sn1 = SelectionNode(selection_id=sel.id, node_id=node1.id, content_hash_at_selection="h1")
    sn2 = SelectionNode(selection_id=sel.id, node_id=node2.id, content_hash_at_selection="h2")
    db_session.add_all([sn1, sn2])
    db_session.commit()

    service = GeminiClientService(db_session)
    prompt = service._reconstruct_prompt(sel)

    assert "Safety Warnings" in prompt
    assert "Don't touch electrical parts." in prompt
    assert "Technical Specifications" in prompt
    assert "Weight: 250g." in prompt


@patch("google.generativeai.GenerativeModel")
def test_gemini_retry_and_fallback(mock_model_class, db_session: Session):
    """
    Simulates consecutive Gemini API failures to test the retry and fallback mechanism.
    We mock generate_content to raise an exception on both attempts.
    """
    # Configure mock
    mock_model = MagicMock()
    mock_model.generate_content.side_effect = Exception("API rate limit exceeded")
    mock_model_class.return_value = mock_model

    doc = Document(title="Manual", device_model="CT-200")
    db_session.add(doc)
    db_session.flush()
    ver = DocumentVersion(document_id=doc.id, version_number=1, filename="v1.pdf", file_hash="hash")
    db_session.add(ver)
    db_session.flush()
    sel = Selection(name="Test", document_version_id=ver.id)
    db_session.add(sel)
    db_session.commit()

    service = GeminiClientService(db_session)
    # Ensure it tries to call the real SDK (bypass use_mock check)
    service.use_mock = False 

    # We expect it to try twice, fail both, and return a fallback list containing TC-FALLBACK
    res_list = service._call_llm_with_retry("Mock prompt", "gemini-1.5-flash")
    
    assert len(res_list.test_cases) == 1
    assert res_list.test_cases[0].id == "TC-FALLBACK"
    assert "Service Fallback" in res_list.test_cases[0].title
    assert mock_model.generate_content.call_count == 2


def test_generation_deduplication(db_session: Session):
    """
    Verifies that duplicate requests (same selection + same hashes within 24h)
    are caught and flagged as cached.
    """
    doc = Document(title="Manual", device_model="CT-200")
    db_session.add(doc)
    db_session.flush()
    ver = DocumentVersion(document_id=doc.id, version_number=1, filename="v1.pdf", file_hash="hash")
    db_session.add(ver)
    db_session.flush()
    sel = Selection(name="Test", document_version_id=ver.id)
    db_session.add(sel)
    db_session.flush()
    sn = SelectionNode(selection_id=sel.id, node_id=1, content_hash_at_selection="hash_v1")
    db_session.add(sn)
    db_session.flush()

    # Create a cached generation row in SQLite
    cached_gen = Generation(
        selection_id=sel.id,
        mongo_document_id="507f1f77bcf86cd799439011", # mock mongo ID
        node_hashes_snapshot="hash_v1", # matches selection hashes
        is_cached=False,
        model_used="gemini-1.5-flash",
        generated_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2),
    )
    db_session.add(cached_gen)
    db_session.commit()

    service = GeminiClientService(db_session)
    res_list, is_cached, model_used = service.generate_qa_test_cases(sel)

    # Should hit deduplication and return is_cached = True
    assert is_cached is True
    assert model_used == "gemini-1.5-flash"
