"""
app/routers/generate.py

FastAPI router for generating QA test cases.
Coordinates selection retrieval, cache checks, LLM generation,
and MongoDB + SQLite persistence.
"""

import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.sqlite import get_db
from app.models.selection import Selection
from app.models.generation import Generation
from app.schemas.generation import GenerationRequest, GenerationResponse
from app.services.llm_client import GeminiClientService
from app.services.llm_store import LLMStoreService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("", response_model=GenerationResponse, status_code=201)
async def generate_test_cases(
    payload: GenerationRequest,
    db: Session = Depends(get_db),
):
    """
    Generates 3-5 QA test case ideas for a given Selection:
    1. Looks up selection and its version-pinned nodes.
    2. Runs deduplication check (24h cache window).
    3. On Cache Hit: Retrieves cases from MongoDB directly (is_cached = True).
    4. On Cache Miss: Calls Gemini, saves cases to MongoDB, and logs generation in SQLite.
    """
    # 1. Fetch Selection from SQLite
    selection = db.query(Selection).filter(Selection.id == payload.selection_id).first()
    if not selection:
        raise HTTPException(
            status_code=404,
            detail=f"Selection with ID {payload.selection_id} not found."
        )

    if not selection.selection_nodes:
        raise HTTPException(
            status_code=400,
            detail="Cannot generate test cases for an empty selection."
        )

    # Instantiate services
    llm_service = GeminiClientService(db)
    mongo_store = LLMStoreService()
    await mongo_store.init_indexes()

    # 2. Call the LLM service layer (which checks for duplicate metadata inside SQLite)
    qa_list, is_cached, model_used = llm_service.generate_qa_test_cases(
        selection, model_override=payload.model_override
    )

    # 3. Handle Cache Hit (Deduplication)
    if is_cached:
        # Find the duplicate record in SQLite to locate the MongoDB document ID
        node_hashes = [sn.content_hash_at_selection for sn in selection.selection_nodes]
        node_hashes.sort()
        hashes_snapshot = "|".join(node_hashes)
        time_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        duplicate = (
            db.query(Generation)
            .filter(
                Generation.selection_id == selection.id,
                Generation.node_hashes_snapshot == hashes_snapshot,
                Generation.generated_at >= time_cutoff
            )
            .order_by(Generation.generated_at.desc())
            .first()
        )

        if not duplicate or not duplicate.mongo_document_id:
            # Fallback if SQLite metadata was found but mongo ID is missing
            raise HTTPException(
                status_code=500,
                detail="Deduplication metadata exists but link to MongoDB is missing."
            )

        # Retrieve full test cases from MongoDB
        test_cases = await mongo_store.get_generation(duplicate.mongo_document_id)
        if test_cases is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve cached test cases from MongoDB."
            )

        return GenerationResponse(
            id=duplicate.id,
            selection_id=selection.id,
            is_cached=True,
            model_used=model_used,
            generated_at=duplicate.generated_at,
            test_cases=test_cases,
        )

    # 4. Handle Cache Miss (Fresh LLM Generation)
    # Save the generated test cases to MongoDB
    node_hashes = [sn.content_hash_at_selection for sn in selection.selection_nodes]
    node_hashes.sort()
    hashes_snapshot = "|".join(node_hashes)

    try:
        mongo_id = await mongo_store.save_generation(
            selection_id=selection.id,
            hashes_snapshot=hashes_snapshot,
            test_cases=qa_list.test_cases,
        )
    except Exception as e:
        logger.error(f"Failed to save generation to MongoDB: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist LLM generation to MongoDB: {str(e)}"
        )

    # Create SQLite generation record to link Selection with MongoDB ID
    db_generation = Generation(
        selection_id=selection.id,
        mongo_document_id=mongo_id,
        node_hashes_snapshot=hashes_snapshot,
        is_cached=False,
        model_used=model_used,
    )
    db.add(db_generation)
    db.commit()
    db.refresh(db_generation)

    return GenerationResponse(
        id=db_generation.id,
        selection_id=selection.id,
        is_cached=False,
        model_used=model_used,
        generated_at=db_generation.generated_at,
        test_cases=qa_list.test_cases,
    )
