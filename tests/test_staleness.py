"""
tests/test_staleness.py

Unit tests for StalenessCheckerService.
Verifies "fresh", "changed", and "deleted" node statuses under document version bumps.
"""

import pytest
from sqlalchemy.orm import Session

from app.db.sqlite import Base, engine, SessionLocal
from app.models.document import Document, DocumentVersion
from app.models.node import Node
from app.models.selection import Selection, SelectionNode
from app.services.staleness_checker import StalenessCheckerService


@pytest.fixture(name="db_session")
def fixture_db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_staleness_checker(db_session: Session):
    """
    Tests all four scenarios:
    1. Pinned version is latest version (all fresh, not stale).
    2. Version 2 has matching node with identical hash (fresh).
    3. Version 2 has matching node with different hash (changed, stale).
    4. Version 2 is missing the node heading path entirely (deleted, stale).
    """
    # Setup document
    doc = Document(title="Manual", device_model="CT-200")
    db_session.add(doc)
    db_session.flush()

    # Ingest Version 1
    v1 = DocumentVersion(document_id=doc.id, version_number=1, filename="v1.pdf", file_hash="h1")
    db_session.add(v1)
    db_session.flush()

    # Node A: Unchanged in v2
    node_a_v1 = Node(
        heading="Safety Overview", level=1, body_text="Do not drop.",
        node_type="heading", content_hash="hash_a", heading_path="Safety Overview",
        document_version_id=v1.id
    )
    # Node B: Changed in v2
    node_b_v1 = Node(
        heading="Calibration Instructions", level=1, body_text="Calibrate daily.",
        node_type="heading", content_hash="hash_b_old", heading_path="Calibration Instructions",
        document_version_id=v1.id
    )
    # Node C: Deleted in v2
    node_c_v1 = Node(
        heading="Warranty Info", level=1, body_text="1 year warranty.",
        node_type="heading", content_hash="hash_c", heading_path="Warranty Info",
        document_version_id=v1.id
    )
    db_session.add_all([node_a_v1, node_b_v1, node_c_v1])
    db_session.flush()

    # Create Selection pinning V1 nodes A, B, and C
    sel = Selection(name="Pin V1 Nodes", document_version_id=v1.id)
    db_session.add(sel)
    db_session.flush()

    sn_a = SelectionNode(selection_id=sel.id, node_id=node_a_v1.id, content_hash_at_selection="hash_a")
    sn_b = SelectionNode(selection_id=sel.id, node_id=node_b_v1.id, content_hash_at_selection="hash_b_old")
    sn_c = SelectionNode(selection_id=sel.id, node_id=node_c_v1.id, content_hash_at_selection="hash_c")
    db_session.add_all([sn_a, sn_b, sn_c])
    db_session.commit()

    # ── Test 1: Check staleness when V1 is the latest version ──
    checker = StalenessCheckerService(db_session)
    report1 = checker.check_selection_staleness(sel)
    
    assert report1.is_stale is False
    assert report1.pinned_version == 1
    assert report1.latest_version == 1
    
    # Map details by heading path
    details_map1 = {d.heading_path: d for d in report1.details}
    assert details_map1["Safety Overview"].status == "fresh"
    assert details_map1["Calibration Instructions"].status == "fresh"
    assert details_map1["Warranty Info"].status == "fresh"

    # ── Setup V2 in database ──
    v2 = DocumentVersion(document_id=doc.id, version_number=2, filename="v2.pdf", file_hash="h2")
    db_session.add(v2)
    db_session.flush()

    # Node A: Unchanged in v2 (same path, same hash)
    node_a_v2 = Node(
        heading="Safety Overview", level=1, body_text="Do not drop.",
        node_type="heading", content_hash="hash_a", heading_path="Safety Overview",
        document_version_id=v2.id
    )
    # Node B: Changed in v2 (same path, different hash)
    node_b_v2 = Node(
        heading="Calibration Instructions", level=1, body_text="Calibrate monthly.",
        node_type="heading", content_hash="hash_b_new", heading_path="Calibration Instructions",
        document_version_id=v2.id
    )
    # Node C: Missing/Deleted in v2 (Warranty Info is not added to v2)
    db_session.add_all([node_a_v2, node_b_v2])
    db_session.commit()

    # ── Test 2: Check staleness when V2 is now the latest version ──
    # Need to reload selection relationship to capture newly added versions on document
    db_session.refresh(sel)
    
    report2 = checker.check_selection_staleness(sel)
    
    assert report2.is_stale is True
    assert report2.pinned_version == 1
    assert report2.latest_version == 2

    # Map details by heading path
    details_map2 = {d.heading_path: d for d in report2.details}
    
    # Node A -> fresh
    assert details_map2["Safety Overview"].status == "fresh"
    assert details_map2["Safety Overview"].latest_node_id == node_a_v2.id
    
    # Node B -> changed
    assert details_map2["Calibration Instructions"].status == "changed"
    assert details_map2["Calibration Instructions"].latest_node_id == node_b_v2.id
    
    # Node C -> deleted
    assert details_map2["Warranty Info"].status == "deleted"
    assert details_map2["Warranty Info"].latest_node_id is None
