"""
app/models/node.py

The Node table is the core of the system.  Every section in a PDF becomes one
row.  The table is self-referential (parent_id → nodes.id) to represent the
heading hierarchy.

Key fields and their rationale
───────────────────────────────
heading         : Section title text as extracted from the PDF.
level           : 1=H1, 2=H2, 3=H3, 4=H4.  Stored explicitly so Browse API
                  can filter by level without walking the parent chain.
body_text       : All body paragraphs + serialised tables under this heading.
node_type       : 'heading' | 'table' | 'figure' | 'caption'.
                  Lets downstream callers filter or render differently.
order_index     : 0-based position among siblings; preserves reading order.
content_hash    : SHA-256(heading.strip() + "\\n" + body_text.strip()).
                  Changing even a single character produces a different hash —
                  intentional for medical device text where precision matters.
heading_path    : Materialised path of heading titles from root to this node
                  joined by " > " (e.g. "Safety > Warnings > Electric Shock").
                  Used by the versioner to match nodes across versions by path
                  without an expensive recursive CTE query.
                  Known limitation: if a heading is renamed AND its content
                  changes in the same version bump, the node appears as deleted
                  + a new node added (no link).  Documented in versioner.py.

Versioning fields
──────────────────
previous_version_node_id : FK to the matching node in the immediately prior
                           version (same heading_path).  NULL if this node is
                           brand-new in this version.
is_changed               : True when content_hash differs from the prior
                           version node's hash.  Computed by the versioner
                           service at ingest time, not at query time.
"""
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.sqlite import Base


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Version linkage ───────────────────────────────────────────────────────
    document_version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Tree structure ────────────────────────────────────────────────────────
    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Content ───────────────────────────────────────────────────────────────
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    node_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="heading"
    )

    # ── Hashing ───────────────────────────────────────────────────────────────
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── Versioning ────────────────────────────────────────────────────────────
    # Materialised path: "Safety > Warnings > Electric Shock"
    # Computed by the parser; stored for O(1) version matching.
    heading_path: Mapped[str] = mapped_column(Text, nullable=False, default="")

    previous_version_node_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    # True  → same path found in previous version but hash differs
    # False → either new node (no prev) or hash is identical (unchanged)
    is_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    document_version: Mapped["DocumentVersion"] = relationship(  # type: ignore[name-defined]
        "DocumentVersion",
        back_populates="nodes",
    )
    parent: Mapped["Node | None"] = relationship(
        "Node",
        remote_side="Node.id",
        back_populates="children",
        foreign_keys=[parent_id],
    )
    children: Mapped[list["Node"]] = relationship(
        "Node",
        back_populates="parent",
        foreign_keys=[parent_id],
        order_by="Node.order_index",
        cascade="all, delete-orphan",
    )
    # Read-only pointer to matched node in previous version
    previous_version_node: Mapped["Node | None"] = relationship(
        "Node",
        foreign_keys=[previous_version_node_id],
        remote_side="Node.id",
        uselist=False,
    )
    selection_nodes: Mapped[list["SelectionNode"]] = relationship(  # type: ignore[name-defined]
        "SelectionNode",
        back_populates="node",
        cascade="all, delete-orphan",
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_nodes_document_version_id", "document_version_id"),
        Index("ix_nodes_parent_id", "parent_id"),
        Index("ix_nodes_content_hash", "content_hash"),
        # heading_path is used for version-to-version matching — must be fast
        Index("ix_nodes_heading_path", "heading_path"),
    )

    def __repr__(self) -> str:
        return (
            f"<Node id={self.id} level={self.level} "
            f"heading='{self.heading[:40]}' hash={self.content_hash[:8]}>"
        )
