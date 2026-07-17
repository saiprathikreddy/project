"""
app/routers/retrieve.py

FastAPI router for Retrieval API.
Retrieves LLM-generated test cases by generation ID or node ID,
enriching the responses with real-time staleness status flags.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.sqlite import get_db
from app.models.generation import Generation
from app.models.selection import Selection, SelectionNode
from app.schemas.retrieve import GenerationWithStalenessResponse, NodeStalenessDetail, StalenessReportResponse
from app.services.llm_store import LLMStoreService
from app.services.staleness_checker import StalenessCheckerService

router = APIRouter()


def _build_staleness_response(report) -> StalenessReportResponse:
    """Helper to convert StalenessCheckerService report to Pydantic schema."""
    details = [
        NodeStalenessDetail(
            node_id=d.node_id,
            heading_path=d.heading_path,
            status=d.status,
            latest_node_id=d.latest_node_id
        )
        for d in report.details
    ]
    return StalenessReportResponse(
        is_stale=report.is_stale,
        pinned_version=report.pinned_version,
        latest_version=report.latest_version,
        details=details
    )


@router.get("/{id}", response_model=GenerationWithStalenessResponse)
async def get_generation_by_id(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Fetches a specific generation record by its SQLite ID,
    loads the full test cases from MongoDB, and calculates staleness status.
    """
    # 1. Fetch metadata from SQLite
    generation = db.query(Generation).filter(Generation.id == id).first()
    if not generation:
        raise HTTPException(
            status_code=404,
            detail=f"Generation with ID {id} not found."
        )

    # 2. Fetch full test cases from MongoDB
    mongo_store = LLMStoreService()
    test_cases = await mongo_store.get_generation(generation.mongo_document_id)
    if test_cases is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve test cases from MongoDB."
        )

    # 3. Calculate staleness using StalenessCheckerService
    staleness_checker = StalenessCheckerService(db)
    staleness_report = staleness_checker.check_selection_staleness(generation.selection)
    
    return GenerationWithStalenessResponse(
        id=generation.id,
        selection_id=generation.selection_id,
        is_cached=generation.is_cached,
        model_used=generation.model_used or "unknown",
        generated_at=generation.generated_at,
        test_cases=test_cases,
        staleness=_build_staleness_response(staleness_report),
    )


@router.get("/nodes/{node_id}", response_model=List[GenerationWithStalenessResponse])
async def get_generations_by_node_id(
    node_id: int,
    db: Session = Depends(get_db),
):
    """
    Retrieves all LLM generations that contain the specified node ID,
    including their respective test cases and staleness reports.
    """
    # Find all generations associated with selections containing the node_id
    generations = (
        db.query(Generation)
        .join(Selection)
        .join(SelectionNode)
        .filter(SelectionNode.node_id == node_id)
        .order_by(Generation.generated_at.desc())
        .all()
    )

    mongo_store = LLMStoreService()
    staleness_checker = StalenessCheckerService(db)
    responses = []

    for gen in generations:
        # Load from MongoDB
        test_cases = await mongo_store.get_generation(gen.mongo_document_id)
        if test_cases is None:
            # Skip if mongo record is corrupted
            continue
            
        # Compute staleness
        staleness_report = staleness_checker.check_selection_staleness(gen.selection)
        
        responses.append(
            GenerationWithStalenessResponse(
                id=gen.id,
                selection_id=gen.selection_id,
                is_cached=gen.is_cached,
                model_used=gen.model_used or "unknown",
                generated_at=gen.generated_at,
                test_cases=test_cases,
                staleness=_build_staleness_response(staleness_report),
            )
        )

    return responses
