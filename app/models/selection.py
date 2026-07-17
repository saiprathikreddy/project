"""
app/models/selection.py

Two tables: Selection + SelectionNode.

Design rationale
────────────────
A Selection is a named, version-pinned basket of nodes.  The user submits
node IDs and specifies (or defaults to) a document version.  We record:

  1. Which document version was current at creation time (version_id).
     This is the "pin" — the selection is semantically tied to the text
     that existed in that version.

  2. The content_hash of each node at the moment of selection creation
     (content_hash_at_selection on SelectionNode).  This is the snapshot
     used by the staleness checker: at retrieval time we compare it against
     the same node's hash in the *latest* version.  If they differ, the
     selection is stale for that node.

Why store the hash snapshot instead of just the version_id?
  The version_id alone only tells us *which* version was pinned.  To detect
  staleness we'd need to join back through nodes, which is an O(N) query per
  retrieval.  The hash snapshot makes staleness a simple string comparison —
  O(1) per node.

Why not store the full text snapshot?
  Content hashes are 64 bytes per node; full text could be kilobytes.
  For staleness detection the hash is sufficient.  The actual text is always
  reconstructable from the node table.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.sqlite import Base


class Selection(Base):
    """
    A named, version-pinned collection of node IDs.

    name            : Human-readable label (e.g. "Battery warnings v1").
    document_version_id : The version that was current when this selection
                      was created.  FK to document_versions.
    """
    __tablename__ = "selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    selection_nodes: Mapped[list["SelectionNode"]] = relationship(
        "SelectionNode",
        back_populates="selection",
        cascade="all, delete-orphan",
    )
    # Convenience back-ref to all generations produced from this selection
    generations: Mapped[list["Generation"]] = relationship(  # type: ignore[name-defined]
        "Generation",
        back_populates="selection",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Selection id={self.id} name='{self.name}' ver={self.document_version_id}>"


class SelectionNode(Base):
    """
    Junction row linking one Selection to one Node.

    content_hash_at_selection:
        Snapshot of the node's content_hash when the selection was created.
        Compared against the current hash at retrieval time to detect staleness.
        Stored here (not on Selection) because each node can have a different
        staleness status within the same selection.
    """
    __tablename__ = "selection_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    selection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("selections.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_hash_at_selection: Mapped[str] = mapped_column(String(64), nullable=False)

    selection: Mapped["Selection"] = relationship(
        "Selection",
        back_populates="selection_nodes",
    )
    node: Mapped["Node"] = relationship(  # type: ignore[name-defined]
        "Node",
        back_populates="selection_nodes",
    )

    __table_args__ = (
        Index("ix_selnodes_selection_id", "selection_id"),
        Index("ix_selnodes_node_id", "node_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<SelectionNode sel={self.selection_id} "
            f"node={self.node_id} hash={self.content_hash_at_selection[:8]}>"
        )
