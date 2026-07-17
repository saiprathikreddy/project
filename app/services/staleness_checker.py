"""
app/services/staleness_checker.py

Service for detecting if a version-pinned selection's nodes have changed
or been deleted in the latest ingested document version.
"""

from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.document import DocumentVersion
from app.models.node import Node
from app.models.selection import Selection
from app.schemas.selection import SelectionResponse


class NodeStalenessStatus:
    def __init__(
        self,
        node_id: int,
        heading_path: str,
        status: str,  # "fresh" | "changed" | "deleted"
        latest_node_id: Optional[int] = None,
    ):
        self.node_id = node_id
        self.heading_path = heading_path
        self.status = status
        self.latest_node_id = latest_node_id

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "heading_path": self.heading_path,
            "status": self.status,
            "latest_node_id": self.latest_node_id,
        }


class SelectionStalenessReport:
    def __init__(
        self,
        is_stale: bool,
        pinned_version: int,
        latest_version: int,
        details: List[NodeStalenessStatus],
    ):
        self.is_stale = is_stale
        self.pinned_version = pinned_version
        self.latest_version = latest_version
        self.details = details

    def to_dict(self) -> dict:
        return {
            "is_stale": self.is_stale,
            "pinned_version": self.pinned_version,
            "latest_version": self.latest_version,
            "details": [d.to_dict() for d in self.details],
        }


class StalenessCheckerService:
    """
    Computes staleness for a selection by comparing its snapshot hashes
    against the nodes of the latest version of the same document.
    """

    def __init__(self, db: Session):
        self.db = db

    def check_selection_staleness(self, selection: Selection) -> SelectionStalenessReport:
        """
        Determines if any of the selection's nodes are stale relative to the latest version.
        
        Algorithm:
        1. Find the latest version of this document.
        2. If selection's pinned version is the latest version, all nodes are "fresh".
        3. Else, map latest nodes by heading_path.
        4. For each selected node, look up by heading_path in the latest version map:
           - Found with same hash -> "fresh"
           - Found with different hash -> "changed"
           - Not found -> "deleted"
        """
        pinned_ver = selection.document_version
        doc = pinned_ver.document
        
        # 1. Get the latest version
        latest_ver = max(doc.versions, key=lambda v: v.version_number)
        
        # 2. Short-circuit if pinned version is already the latest
        if pinned_ver.id == latest_ver.id:
            details = [
                NodeStalenessStatus(
                    node_id=sn.node_id,
                    heading_path=sn.node.heading_path,
                    status="fresh",
                    latest_node_id=sn.node_id
                )
                for sn in selection.selection_nodes
            ]
            return SelectionStalenessReport(
                is_stale=False,
                pinned_version=pinned_ver.version_number,
                latest_version=latest_ver.version_number,
                details=details
            )

        # 3. Fetch and map latest nodes by heading_path
        latest_nodes = (
            self.db.query(Node)
            .filter(Node.document_version_id == latest_ver.id)
            .all()
        )
        latest_map: Dict[str, Node] = {n.heading_path: n for n in latest_nodes}

        # 4. Compare each node in the selection
        details = []
        is_stale = False

        for sel_node in selection.selection_nodes:
            original_node = sel_node.node
            latest_match = latest_map.get(original_node.heading_path)

            if latest_match:
                if latest_match.content_hash != sel_node.content_hash_at_selection:
                    # Content changed in latest version
                    status = "changed"
                    is_stale = True
                else:
                    # Content remains identical
                    status = "fresh"
                latest_node_id = latest_match.id
            else:
                # Node path is missing in latest version (renamed or deleted)
                status = "deleted"
                is_stale = True
                latest_node_id = None

            details.append(
                NodeStalenessStatus(
                    node_id=original_node.id,
                    heading_path=original_node.heading_path,
                    status=status,
                    latest_node_id=latest_node_id
                )
            )

        return SelectionStalenessReport(
            is_stale=is_stale,
            pinned_version=pinned_ver.version_number,
            latest_version=latest_ver.version_number,
            details=details
        )
