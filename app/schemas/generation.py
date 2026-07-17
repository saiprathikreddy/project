"""
app/schemas/generation.py

Pydantic schemas for LLM Generation API requests/responses and structured JSON outputs.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TestCase(BaseModel):
    id: str = Field(..., description="Unique test case ID (e.g. TC-001)")
    title: str = Field(..., description="Short descriptive title of the test case")
    description: str = Field(..., description="Details on what this test case is verifying")
    preconditions: List[str] = Field(..., description="Prerequisites needed before executing the test")
    steps: List[str] = Field(..., description="Chronological execution steps")
    expected_result: str = Field(..., description="The expected outcome of the steps")


class QATestCaseList(BaseModel):
    test_cases: List[TestCase] = Field(..., description="List of generated QA test cases")


class GenerationRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    selection_id: int = Field(..., description="The ID of the selection to generate test cases for")
    # Optional parameters to allow tweaking Gemini model if needed
    model_override: Optional[str] = Field(None, description="Optionally override the Gemini model used")


class GenerationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    selection_id: int
    is_cached: bool
    model_used: str
    generated_at: datetime
    test_cases: List[TestCase]
