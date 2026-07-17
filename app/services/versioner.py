"""
app/services/versioner.py

Business logic for matching nodes across document versions.

Where this matching strategy breaks (IMPORTANT):
────────────────────────────────────────────────
Our matching strategy relies entirely on the `heading_path` of the nodes
(e.g., "Introduction > Safety"). This O(1) matching is clean, but breaks under
the following scenarios:

  1. Heading Rename:
     If a section heading is renamed (e.g., "Safety Warnings" becomes "Safety Info"),
     the `heading_path` changes. The system will treat "Safety Warnings" as
     deleted and "Safety Info" as a brand-new node, even if the body text remains
     100% identical.

  2. Cascading Parent Rename:
     If a parent node is renamed (e.g., H1 "Setup" becomes "Installation"),
     every single child node underneath it gets a new `heading_path`
     (e.g., "Setup > Battery" becomes "Installation > Battery"). This causes
     the entire sub-tree to match as deleted and re-added.

  3. Structural Moves:
     If a node is moved to a different parent (e.g., from "Setup > Battery" to
     "Maintenance > Battery"), its path changes and it loses its version history link.

Robust alternatives for future implementation:
  - Edit distance / TF-IDF: If no exact path match is found, compare body texts
    using a similarity threshold (e.g., Levenshtein distance) to detect renames.
  - Anchor IDs: Embed persistent, hidden markup IDs in the source documents.
"""

from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.node import Node
from app.services.parser import ParsedNode


class DocumentVersioner:
    """
    Handles version-to-version matching for ingested nodes.
    Matches nodes from a new version to nodes in a previous version.
    """

    def __init__(self, db: Session):
        self.db = db

    def match_and_link_nodes(
        self,
        new_db_nodes: List[Node],
        previous_version_id: Optional[int]
    ) -> None:
        """
        Matches nodes in new_db_nodes with nodes from the previous_version_id by heading_path.
        Sets:
          - previous_version_node_id
          - is_changed
        on the new nodes in-place.
        """
        if not previous_version_id:
            # First version, nothing to match against
            for node in new_db_nodes:
                node.previous_version_node_id = None
                node.is_changed = False
            return

        # Fetch all nodes from the previous version
        prev_nodes = (
            self.db.query(Node)
            .filter(Node.document_version_id == previous_version_id)
            .all()
        )

        # Map previous nodes by heading_path for O(1) lookup
        prev_map: Dict[str, Node] = {n.heading_path: n for n in prev_nodes}

        for node in new_db_nodes:
            # Look up by heading path
            matched_prev = prev_map.get(node.heading_path)

            if matched_prev:
                # Link to previous node
                node.previous_version_node_id = matched_prev.id
                
                # Check if content has changed (comparing pre-computed SHA-256 hashes)
                if node.content_hash != matched_prev.content_hash:
                    node.is_changed = True
                else:
                    node.is_changed = False
            else:
                # Brand new node in this version
                node.previous_version_node_id = None
                node.is_changed = False
