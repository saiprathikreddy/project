"""
app/models/generation.py

The Generation table is the SQLite side of the LLM output story.

Split storage design
─────────────────────
LLM output lives in two places:

  1. This table (SQLite) — lightweight index record:
       - Links to the selection that triggered the generation
       - Stores the MongoDB document ID so we can fetch the full payload
       - Records deduplication state (is_cached)
       - Records the set of content hashes the generation was based on
         (node_hashes_snapshot) — used by the staleness checker

  2. MongoDB — full payload (the actual QA test cases as JSON).
     Stored there because:
       a) The QA JSON structure is document-like and schema-flexible
          (number of test cases, nested steps, etc.)
       b) MongoDB's document model handles arbitrary-depth JSON without
          a rigid column schema
       c) Keeps the SQLite schema from ballooning with TEXT blobs

node_hashes_snapshot
─────────────────────
A pipe-separated concatenation of the content_hashes of every node in the
selection at generation time, sorted and joined:
    "abc123|def456|..."
This is the deduplication key: a new generation request for the same
selection_id is a duplicate if its sorted hash set matches an existing
generation's snapshot within the dedup window (24 hours, enforced in
llm_client.py).

is_cached
──────────
True when this generation row was returned from cache (no LLM call was made).
Exposed in the API response so callers know they got a cached result.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.sqlite import Base


class Generation(Base):
    """
    SQLite index record for one LLM generation event.
    The full QA JSON payload is stored in MongoDB under mongo_document_id.
    """
    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    selection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("selections.id", ondelete="CASCADE"),
        nullable=False,
    )
    # MongoDB ObjectId as a string (24-char hex)
    mongo_document_id: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Pipe-joined sorted content_hashes of the selected nodes at gen time.
    # Used for deduplication: same selection_id + same snapshot = duplicate.
    node_hashes_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    # True if this row was returned from cache (no LLM call)
    is_cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Which Gemini model was called (for audit / reproducibility)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    selection: Mapped["Selection"] = relationship(  # type: ignore[name-defined]
        "Selection",
        back_populates="generations",
    )

    __table_args__ = (
        Index("ix_generations_selection_id", "selection_id"),
        # Fast lookup of recent generations by selection for dedup check
        Index("ix_generations_selection_generated", "selection_id", "generated_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Generation id={self.id} sel={self.selection_id} "
            f"cached={self.is_cached} mongo={self.mongo_document_id}>"
        )
