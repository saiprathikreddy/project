"""
app/services/llm_store.py

Service layer for persisting and retrieving LLM-generated test cases in MongoDB.
Uses the async Motor client.
"""

from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId

from app.db.mongo import get_mongo_db
from app.schemas.generation import TestCase


class LLMStoreService:
    """
    Manages MongoDB persistence for generated QA test cases.
    """

    def __init__(self):
        self.db = get_mongo_db()
        self.collection = self.db["generations"]

    async def init_indexes(self) -> None:
        """Creates indexes on the collection for rapid deduplication searches."""
        # Compound index for selection_id and hashes_snapshot
        await self.collection.create_index([
            ("selection_id", 1),
            ("node_hashes_snapshot", 1)
        ], name="idx_selection_hashes")

    async def save_generation(
        self,
        selection_id: int,
        hashes_snapshot: str,
        test_cases: List[TestCase]
    ) -> str:
        """
        Saves generated test cases to MongoDB.
        Returns the hex string representation of the inserted ObjectId.
        """
        # Convert Pydantic models to dictionaries
        cases_dict = [tc.model_dump() for tc in test_cases]
        
        doc = {
            "selection_id": selection_id,
            "node_hashes_snapshot": hashes_snapshot,
            "test_cases": cases_dict,
            "generated_at": datetime.now(timezone.utc),
        }
        
        result = await self.collection.insert_one(doc)
        return str(result.inserted_id)

    async def get_generation(self, mongo_id: str) -> Optional[List[TestCase]]:
        """
        Retrieves generated test cases from MongoDB by hex string ObjectId.
        """
        try:
            obj_id = ObjectId(mongo_id)
        except Exception:
            return None

        doc = await self.collection.find_one({"_id": obj_id})
        if not doc or "test_cases" not in doc:
            return None

        # Validate and convert back to Pydantic TestCase models
        return [TestCase.model_validate(tc) for tc in doc["test_cases"]]
