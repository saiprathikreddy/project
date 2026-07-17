"""
app/services/llm_store.py

Service layer for persisting and retrieving LLM-generated test cases in MongoDB.
Uses the async Motor client.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from bson import ObjectId

from app.db.mongo import get_mongo_db
from app.schemas.generation import TestCase


class LLMStoreService:
    """
    Manages MongoDB persistence for generated QA test cases.
    Includes a transparent JSON file fallback if MongoDB is offline.
    """

    def __init__(self):
        self.db = get_mongo_db()
        self.collection = self.db["generations"]
        self.fallback_file = Path("data/mongodb_fallback.json")
        self.fallback_active = False

    async def init_indexes(self) -> None:
        """Creates indexes on the collection for rapid deduplication searches."""
        try:
            # Short timeout to detect offline status quickly
            await self.collection.database.client.admin.command('ping', timeoutMS=2000)
            await self.collection.create_index([
                ("selection_id", 1),
                ("node_hashes_snapshot", 1)
            ], name="idx_selection_hashes")
        except Exception:
            logger.warning("MongoDB connection check failed. Activating local JSON fallback storage.")
            self.fallback_active = True

    async def save_generation(
        self,
        selection_id: int,
        hashes_snapshot: str,
        test_cases: List[TestCase]
    ) -> str:
        """
        Saves generated test cases to MongoDB. Falls back to JSON file if offline.
        Returns the hex string representation of the inserted ObjectId.
        """
        cases_dict = [tc.model_dump() for tc in test_cases]
        doc = {
            "selection_id": selection_id,
            "node_hashes_snapshot": hashes_snapshot,
            "test_cases": cases_dict,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        if not self.fallback_active:
            try:
                result = await self.collection.insert_one(doc)
                return str(result.inserted_id)
            except Exception as e:
                logger.warning(f"MongoDB write failed: {str(e)}. Falling back to local file.")
                self.fallback_active = True

        # Fallback implementation: write to local JSON file
        new_id = str(ObjectId())
        doc["_id"] = new_id

        # Read existing fallback records
        fallback_data = {}
        if self.fallback_file.exists():
            try:
                with open(self.fallback_file, "r") as f:
                    fallback_data = json.load(f)
            except Exception:
                pass

        fallback_data[new_id] = doc

        # Save back to file
        self.fallback_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.fallback_file, "w") as f:
            json.dump(fallback_data, f, indent=4)

        return new_id

    async def get_generation(self, mongo_id: str) -> Optional[List[TestCase]]:
        """
        Retrieves generated test cases from MongoDB or local JSON fallback.
        """
        if not self.fallback_active:
            try:
                obj_id = ObjectId(mongo_id)
                doc = await self.collection.find_one({"_id": obj_id})
                if doc and "test_cases" in doc:
                    return [TestCase.model_validate(tc) for tc in doc["test_cases"]]
            except Exception as e:
                logger.warning(f"MongoDB read failed: {str(e)}. Checking local file.")
                self.fallback_active = True

        # Fallback implementation: read from local JSON file
        if self.fallback_file.exists():
            try:
                with open(self.fallback_file, "r") as f:
                    fallback_data = json.load(f)
                doc = fallback_data.get(mongo_id)
                if doc and "test_cases" in doc:
                    return [TestCase.model_validate(tc) for tc in doc["test_cases"]]
            except Exception:
                pass

        return None
