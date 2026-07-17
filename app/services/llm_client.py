"""
app/services/llm_client.py

Gemini LLM Client Service.
Handles prompt construction, calling the Gemini API, structured JSON parsing,
retries on malformed responses, fallback outputs, and checking for duplicates.
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import google.generativeai as genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models.generation import Generation
from app.models.node import Node
from app.models.selection import Selection
from app.schemas.generation import QATestCaseList, TestCase

logger = logging.getLogger(__name__)


class GeminiClientService:
    """
    Service for communicating with Gemini API to generate QA test cases.
    """

    def __init__(self, db: Session):
        self.db = db
        # Set up Gemini client configuration
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        
        # Configure the genai SDK if API key is provided and not in test/mock mode
        self.use_mock = (
            settings.APP_ENV == "testing"
            or not self.api_key
            or self.api_key == "your_key_here"
            or self.api_key == ""
        )
        if not self.use_mock:
            genai.configure(api_key=self.api_key)

    def generate_qa_test_cases(
        self,
        selection: Selection,
        model_override: Optional[str] = None
    ) -> Tuple[QATestCaseList, bool, str]:
        """
        Generates 3-5 QA test cases for a selection.
        Enforces a 24-hour cache deduplication policy.
        
        Returns:
            Tuple[QATestCaseList, is_cached: bool, model_used: str]
        """
        model_used = model_override or self.model_name
        
        # 1. Compute node hashes snapshot for deduplication
        node_hashes = [sn.content_hash_at_selection for sn in selection.selection_nodes]
        # Sort hashes to ensure order-independence
        node_hashes.sort()
        hashes_snapshot = "|".join(node_hashes)

        # 2. Check for duplicate generations within the last 24 hours
        time_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        duplicate = (
            self.db.query(Generation)
            .filter(
                Generation.selection_id == selection.id,
                Generation.node_hashes_snapshot == hashes_snapshot,
                Generation.generated_at >= time_cutoff
            )
            .order_by(Generation.generated_at.desc())
            .first()
        )
        
        if duplicate and duplicate.mongo_document_id:
            # We found a duplicate! Return cached status.
            # Note: The actual fetching of test cases from MongoDB is handled
            # by the generate router or a combined service. Here we just flag it.
            logger.info(f"Duplicate generation request found (Generation ID {duplicate.id}). Returning cache.")
            
            # Retrieve from mongo (we'll fetch from mongo in router, but let's provide a mock/stub check)
            # For this service layer, we will let the caller know it is cached.
            # To make this class complete, we can import motor/pymongo to retrieve it directly,
            # or return the cached status so the router loads it. Let's return empty/placeholder
            # and let the caller fetch the full cases using the mongo ID.
            # However, to be fully functional, let's look up the cached data from MongoDB synchronously if we can.
            # (Note: Step 10 is MongoDB persistence, we will link it then. For now, return is_cached=True).
            return QATestCaseList(test_cases=[]), True, duplicate.model_used or model_used

        # 3. Reconstruct prompt text
        prompt = self._reconstruct_prompt(selection)

        # 4. Generate content with Gemini (with retry and fallback)
        test_cases_list = self._call_llm_with_retry(prompt, model_used)

        return test_cases_list, False, model_used

    def _reconstruct_prompt(self, selection: Selection) -> str:
        """Conposes prompt text from the pinned nodes in the selection."""
        prompt_parts = [
            "You are an expert QA Engineer specializing in software, firmware, and hardware verification for medical devices.",
            "Analyze the medical device manual sections below and generate 3 to 5 structured QA test case ideas.",
            "Focus on safety warnings, specifications, calibration instructions, and operational procedures.",
            "Each test case must verify specific conditions, inputs, and expected outcomes.",
            "\n=== DEVICE MANUAL EXTRACTS ==="
        ]
        
        # Load the selected nodes and their version-pinned content
        for sel_node in selection.selection_nodes:
            node = sel_node.node
            prompt_parts.append(f"\nSection: {node.heading_path}")
            prompt_parts.append(f"Type: {node.node_type}")
            if node.body_text:
                prompt_parts.append(f"Content:\n{node.body_text}")
            prompt_parts.append("-" * 40)
            
        prompt_parts.append(
            "\nGenerate 3-5 QA test case ideas as a valid JSON object matching the requested schema."
        )
        return "\n".join(prompt_parts)

    def _call_llm_with_retry(self, prompt: str, model_name: str) -> QATestCaseList:
        """
        Calls Gemini, retrying once on API errors or parsing failures.
        Falls back to a safe default schema if both attempts fail.
        """
        if self.use_mock:
            logger.info("Using mock Gemini client service (mocking test case generation).")
            return self._generate_mock_test_cases()

        attempts = 2
        for attempt in range(attempts):
            try:
                logger.info(f"Calling Gemini API (model: {model_name}), attempt {attempt + 1}...")
                model = genai.GenerativeModel(model_name)
                
                # Request JSON output adhering to QATestCaseList Pydantic schema
                # Pydantic is supported natively by the genai SDK as a schema constraint
                response = model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                        response_schema=QATestCaseList,
                    )
                )
                
                if not response.text:
                    raise ValueError("Gemini returned empty response text.")
                
                # Parse and validate against Pydantic schema
                data = json.loads(response.text)
                qa_list = QATestCaseList.model_validate(data)
                
                logger.info(f"Successfully generated {len(qa_list.test_cases)} test cases.")
                return qa_list
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < attempts - 1:
                    time.sleep(1.0)  # Wait 1s before retrying
                else:
                    logger.error("All Gemini API attempts failed. Falling back to default test case.")
                    return self._generate_fallback_test_case(f"Gemini call failed: {str(e)}")

        return self._generate_fallback_test_case("LLM call failed.")

    def _generate_mock_test_cases(self) -> QATestCaseList:
        """Helper returning high-quality mock test cases for testing/development."""
        return QATestCaseList(
            test_cases=[
                TestCase(
                    id="TC-001",
                    title="Verify blood pressure measurement boundary limits",
                    description="Verifies that the CT-200 blood pressure monitor displays an out-of-range error if pressure exceeds 300 mmHg.",
                    preconditions=["Device is calibrated and powered on.", "Simulated cuff pressure generator connected."],
                    steps=[
                        "Turn on the device.",
                        "Simulate cuff pressure rising to 310 mmHg.",
                        "Observe the display error code."
                    ],
                    expected_result="Device shows Error Code 'E-2' on display and halts pump motor immediately."
                ),
                TestCase(
                    id="TC-002",
                    title="Verify low battery safety warning trigger",
                    description="Ensures a low battery indicator displays when battery voltage drops below 2.2V.",
                    preconditions=["DC voltage simulator source connected to battery terminals.", "Voltage set to nominal 3.0V."],
                    steps=[
                        "Power on the CT-200.",
                        "Gradually decrease simulator voltage to 2.1V.",
                        "Observe battery icon indicator status on screen."
                    ],
                    expected_result="Battery indicator icon flashes on LCD screen, and warning beep sounds twice."
                ),
                TestCase(
                    id="TC-003",
                    title="Verify measurement abort button interrupt",
                    description="Ensures the user can cancel measurement at any time during cuff inflation.",
                    preconditions=["Cuff wrapped around patient simulator.", "Measurement started."],
                    steps=[
                        "Press start button to begin inflation.",
                        "While cuff is inflating, press the Power/Stop button.",
                        "Verify cuff deflation and motor status."
                    ],
                    expected_result="Inflation motor stops instantly, cuff releases pressure completely in under 2 seconds, screen shows 'OP-ERR'."
                )
            ]
        )

    def _generate_fallback_test_case(self, error_msg: str) -> QATestCaseList:
        """Safe fallback to return when the LLM service is completely unavailable."""
        return QATestCaseList(
            test_cases=[
                TestCase(
                    id="TC-FALLBACK",
                    title="Service Fallback - QA Generator Offline",
                    description=f"Automated fallback case generated due to LLM interface failure. Details: {error_msg}",
                    preconditions=["Gemini generation service experienced an error."],
                    steps=[
                        "Verify your Internet connection.",
                        "Check that the GEMINI_API_KEY environment variable is configured and active.",
                        "Verify Gemini rate limits have not been exceeded."
                    ],
                    expected_result="Gemini service becomes available and successfully returns structured JSON test cases."
                )
            ]
        )
