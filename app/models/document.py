"""
app/models/document.py

Two tables: Document + DocumentVersion.

Why split Document from DocumentVersion?
  A "Document" is the logical entity (the CT-200 manual). A "DocumentVersion"
  is one ingested PDF file. This lets us:
    - Store metadata that is stable across versions (title, device model) on
      Document without duplicating it per version.
    - Query "latest version of document X" with a simple ORDER BY without
      needing a separate lookup table.
    - Support multiple documents in the future (e.g. CT-100, CT-300) without
      any schema change.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.sqlite import Base


class Document(Base):
    """
    Logical document entity (e.g. "CT-200 Blood Pressure Monitor Manual").
    One document has many versions.
    """
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    device_model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    versions: Mapped[list["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        order_by="DocumentVersion.version_number",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} model='{self.device_model}'>"


class DocumentVersion(Base):
    """
    One ingested PDF file.  version_number is monotonically increasing per
    document and is set by the ingest service (max existing + 1).

    filename: original filename as uploaded (preserved for audit trail).
    file_hash: SHA-256 of the raw PDF bytes.  Used to detect re-uploads of
               an identical file and skip re-ingestion cheaply.
    """
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # SHA-256 of raw PDF bytes — used to skip duplicate uploads
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="versions",
    )
    nodes: Mapped[list["Node"]] = relationship(  # type: ignore[name-defined]
        "Node",
        back_populates="document_version",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Each (document, version_number) pair must be unique.
        UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentVersion id={self.id} "
            f"doc={self.document_id} v{self.version_number}>"
        )
