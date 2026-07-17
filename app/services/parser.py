"""
app/services/parser.py

PDF parser service using PyMuPDF (fitz) to extract document structure,
heading hierarchy, tables, figures, and content hashes.
"""

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF


class ParsedNode:
    """
    In-memory representation of a parsed document node before it is saved
    in the database.
    """
    def __init__(
        self,
        heading: str,
        level: int,
        body_text: str = "",
        node_type: str = "heading",
        order_index: int = 0,
        heading_path: str = "",
    ):
        self.heading = heading
        self.level = level
        self.body_text = body_text
        self.node_type = node_type
        self.order_index = order_index
        self.heading_path = heading_path
        self.parent: Optional[ParsedNode] = None
        self.children: List[ParsedNode] = []
        self.content_hash = ""

    def add_child(self, child: "ParsedNode") -> None:
        child.parent = self
        child.order_index = len(self.children)
        # Re-compute heading path based on parent path
        if self.heading_path:
            child.heading_path = f"{self.heading_path} > {child.heading}"
        else:
            child.heading_path = child.heading
        self.children.append(child)

    def calculate_hash(self) -> str:
        """
        Computes SHA-256 hash of the node's heading and body text.
        Whitespace edits are included (intentional for high-precision medical text).
        """
        raw_content = f"{self.heading.strip()}\n{self.body_text.strip()}"
        self.content_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        return self.content_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "heading": self.heading,
            "level": self.level,
            "body_text": self.body_text,
            "node_type": self.node_type,
            "order_index": self.order_index,
            "heading_path": self.heading_path,
            "content_hash": self.content_hash or self.calculate_hash(),
            "children": [c.to_dict() for c in self.children],
        }

    def __repr__(self) -> str:
        return f"<ParsedNode level={self.level} heading='{self.heading[:30]}' path='{self.heading_path[:40]}'>"


class PDFParser:
    """
    Parses a medical device manual PDF into a hierarchical tree.
    Uses font sizes and bold attributes to identify heading levels.
    """

    def __init__(self, pdf_path: Path):
        self.pdf_path = pdf_path
        # Normal heading level thresholds (computed dynamically or fallback)
        self.body_font_size = 10.0
        self.heading_thresholds: List[float] = []

    def parse(self, analyze_fonts: bool = True) -> ParsedNode:
        """
        Reads the PDF and returns the root ParsedNode.
        """
        doc = fitz.open(self.pdf_path)
        
        # 1. Analyze font size distribution across the document to set thresholds
        if analyze_fonts:
            self._analyze_fonts(doc)

        # 2. Build the hierarchical tree
        root = ParsedNode(heading="Root", level=0, heading_path="")
        stack: List[ParsedNode] = [root]

        # Duplicate heading counter to handle identical headings at same level
        # format: {heading_path: count}
        path_counts: Dict[str, int] = {}

        for page_idx, page in enumerate(doc):
            # Extract tables first to map their bounding boxes
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables]

            # Process normal text blocks
            blocks = page.get_text("dict")["blocks"]
            
            # Sort blocks from top to bottom, left to right
            blocks.sort(key=lambda b: (b.get("bbox", (0, 0, 0, 0))[1], b.get("bbox", (0, 0, 0, 0))[0]))

            # Keep track of table index to merge tables at the right positions
            table_inserted_indices = set()

            for block in blocks:
                # 0 is text, 1 is image (figure)
                if block.get("type") == 1:
                    # Handle figure
                    bbox = block.get("bbox", (0, 0, 0, 0))
                    fig_node = ParsedNode(
                        heading=f"Figure on Page {page_idx + 1}",
                        level=stack[-1].level + 1,
                        node_type="figure",
                    )
                    # Figure caption is often the next block, we will set heading if it matches a pattern
                    stack[-1].add_child(fig_node)
                    fig_node.calculate_hash()
                    continue

                if block.get("type") != 0:
                    continue

                # Check if this text block is inside a table
                block_bbox = block.get("bbox", (0, 0, 0, 0))
                if self._is_bbox_inside_tables(block_bbox, table_bboxes):
                    # We will handle tables separately using page.find_tables() and extract()
                    # Find which table this belongs to and insert it if not already done
                    for tbl_idx, tbl in enumerate(tables):
                        if tbl_idx not in table_inserted_indices and self._is_bbox_intersecting(block_bbox, tbl.bbox):
                            table_node = self._extract_table_node(tbl, page_idx, stack[-1])
                            # Append table markdown to the current heading's body_text
                            if stack[-1].body_text:
                                stack[-1].body_text += "\n\n" + table_node.body_text
                            else:
                                stack[-1].body_text = table_node.body_text
                            stack[-1].add_child(table_node)
                            table_node.calculate_hash()
                            table_inserted_indices.add(tbl_idx)
                    continue

                # Parse lines and spans
                for line in block.get("lines", []):
                    # Combine spans in a line
                    line_text = ""
                    max_font_size = 0.0
                    is_bold = False

                    for span in line.get("spans", []):
                        span_text = span.get("text", "")
                        if not span_text.strip():
                            continue
                        line_text += " " + span_text
                        
                        font_size = span.get("size", 10.0)
                        if font_size > max_font_size:
                            max_font_size = font_size
                        
                        # Font flags: bit 4 (value 16) is bold in PyMuPDF
                        flags = span.get("flags", 0)
                        if flags & 16 or "bold" in span.get("font", "").lower():
                            is_bold = True

                    line_text = line_text.strip()
                    if not line_text:
                        continue

                    # Heading level classification
                    heading_level = self._classify_heading(line_text, max_font_size, is_bold)

                    if heading_level:
                        # Normalize level jumps (e.g. from level 1 straight to level 3)
                        # We clamp heading_level to at most stack[-1].level + 1
                        current_parent_level = stack[-1].level
                        if heading_level > current_parent_level + 1:
                            heading_level = current_parent_level + 1

                        # Pop the stack until we find the parent (which has a lower level than heading_level)
                        while len(stack) > 1 and stack[-1].level >= heading_level:
                            stack.pop()

                        # Construct heading path and deduplicate
                        base_path = stack[-1].heading_path
                        temp_path = f"{base_path} > {line_text}" if base_path else line_text
                        
                        # Deduplicate heading at same level
                        if temp_path in path_counts:
                            path_counts[temp_path] += 1
                            display_heading = f"{line_text} ({path_counts[temp_path]})"
                        else:
                            path_counts[temp_path] = 1
                            display_heading = line_text

                        new_node = ParsedNode(
                            heading=display_heading,
                            level=heading_level,
                            node_type="heading",
                        )
                        stack[-1].add_child(new_node)
                        stack.append(new_node)
                    else:
                        # Append text to the current leaf node in the stack
                        current_node = stack[-1]
                        if current_node.body_text:
                            current_node.body_text += "\n" + line_text
                        else:
                            current_node.body_text = line_text

            # Handle any tables on this page that weren't intercepted by text blocks
            for tbl_idx, tbl in enumerate(tables):
                if tbl_idx not in table_inserted_indices:
                    table_node = self._extract_table_node(tbl, page_idx, stack[-1])
                    # Append table markdown to the current heading's body_text
                    if stack[-1].body_text:
                        stack[-1].body_text += "\n\n" + table_node.body_text
                    else:
                        stack[-1].body_text = table_node.body_text
                    stack[-1].add_child(table_node)
                    table_node.calculate_hash()
                    table_inserted_indices.add(tbl_idx)

        # Post-parse: calculate hashes recursively
        self._calculate_all_hashes(root)
        doc.close()
        return root

    def _analyze_fonts(self, doc: fitz.Document) -> None:
        """
        Scans a sample of pages to determine font size distribution.
        Sets thresholds for heading levels H1, H2, H3.
        """
        font_sizes: List[float] = []
        # Count font size occurrences weighted by character length
        from collections import defaultdict
        size_char_counts = defaultdict(int)

        for page in doc:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        sz = span.get("size", 10.0)
                        txt = span.get("text", "")
                        if sz > 0 and txt.strip():
                            rounded_sz = round(float(sz), 1)
                            # Weight by number of characters in the span
                            size_char_counts[rounded_sz] += len(txt)
                            font_sizes.extend([rounded_sz] * len(txt))

        if not font_sizes:
            # Fallbacks
            self.body_font_size = 10.0
            self.heading_thresholds = [18.0, 14.0, 12.0]
            return

        # Body font size is the one with the most characters in the document
        self.body_font_size = max(size_char_counts, key=size_char_counts.get)

        # Filter out sizes smaller than body font size
        heading_sizes = sorted([sz for sz in font_sizes if sz > self.body_font_size + 0.5])
        
        if not heading_sizes:
            # Default thresholds if no text is larger than body
            self.heading_thresholds = [self.body_font_size + 6, self.body_font_size + 4, self.body_font_size + 2]
            return

        # Use percentiles to find thresholds
        n = len(heading_sizes)
        h1_val = heading_sizes[int(n * 0.95)]
        h2_val = heading_sizes[int(n * 0.85)]
        h3_val = heading_sizes[int(n * 0.70)]

        # Ensure strict hierarchy and separation
        h1_val = max(h1_val, self.body_font_size + 5.0)
        h2_val = max(h2_val, self.body_font_size + 3.0)
        h3_val = max(h3_val, self.body_font_size + 1.5)

        # If they collapsed, nudge them
        if h2_val >= h1_val:
            h2_val = h1_val - 2.0
        if h3_val >= h2_val:
            h3_val = h2_val - 2.0

        self.heading_thresholds = [h1_val, h2_val, h3_val]

    def _classify_heading(self, text: str, font_size: float, is_bold: bool) -> Optional[int]:
        """
        Classifies a line of text as heading (1-4) or None (body).
        Uses simple heuristic rules including common manual patterns (e.g. numbered headings like "1.1").
        """
        # Headings are generally not extremely long (e.g., less than 150 chars)
        if len(text) > 150:
            return None

        # Check font size against thresholds
        h1, h2, h3 = self.heading_thresholds
        
        # Explicit patterns (e.g., "1. Safety Information", "2.1 Specifications")
        heading_num_pattern = re.match(r'^(\d+\.\d*)\s+[A-Z]', text)
        is_numbered = bool(heading_num_pattern)

        if font_size >= h1:
            return 1
        elif font_size >= h2:
            return 2
        elif font_size >= h3:
            return 3
        elif font_size >= self.body_font_size + 1.0 and (is_bold or is_numbered):
            return 4
        elif is_bold and is_numbered:
            return 4

        return None

    def _is_bbox_inside_tables(self, bbox: tuple, table_bboxes: List[tuple]) -> bool:
        """Checks if a bounding box falls inside any of the table bounding boxes."""
        bx0, by0, bx1, by1 = bbox
        for tx0, ty0, tx1, ty1 in table_bboxes:
            # Overlap/containment check
            if bx0 >= tx0 - 2 and bx1 <= tx1 + 2 and by0 >= ty0 - 2 and by1 <= ty1 + 2:
                return True
        return False

    def _is_bbox_intersecting(self, bbox1: tuple, bbox2: tuple) -> bool:
        """Checks if two bounding boxes intersect."""
        x1_min, y1_min, x1_max, y1_max = bbox1
        x2_min, y2_min, x2_max, y2_max = bbox2
        return not (x1_max < x2_min or x2_max < x1_min or y1_max < y2_min or y2_max < y1_min)

    def _extract_table_node(self, table: Any, page_idx: int, parent_node: ParsedNode) -> ParsedNode:
        """
        Extracts a table's contents and represents it as Markdown.
        """
        data = table.extract()
        # Convert list of lists to Markdown table
        md_lines = []
        if data:
            # Clean cells
            cleaned_data = [[(cell or "").strip().replace("\n", " ") for cell in row] for row in data]
            
            # Header
            headers = cleaned_data[0]
            md_lines.append("| " + " | ".join(headers) + " |")
            
            # Separator
            seps = ["---" for _ in headers]
            md_lines.append("| " + " | ".join(seps) + " |")
            
            # Body rows
            for row in cleaned_data[1:]:
                md_lines.append("| " + " | ".join(row) + " |")

        table_md = "\n".join(md_lines)
        
        # Table heading/caption detection could be added, but a generic name works well
        table_node = ParsedNode(
            heading=f"Table {page_idx + 1}-{parent_node.order_index + 1}",
            level=parent_node.level + 1,
            body_text=table_md,
            node_type="table",
        )
        return table_node

    def _calculate_all_hashes(self, node: ParsedNode) -> None:
        """
        Recursively calculates content hashes for all nodes.
        """
        node.calculate_hash()
        for child in node.children:
            self._calculate_all_hashes(child)
