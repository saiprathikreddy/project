"""
tests/test_parser.py

Unit tests for PDFParser verifying the three edge cases:
1. Duplicate headings at the same level (disambiguated with suffix, e.g. " (2)")
2. Tables embedded in sections (correctly converted to Markdown table nodes)
3. Inconsistent heading levels (normalized to parent + 1 without skipping levels)
"""

import tempfile
from pathlib import Path
import pytest
import fitz

from app.services.parser import PDFParser


def create_mock_pdf(pdf_path: Path, items: list) -> None:
    """
    Creates a temporary PDF using PyMuPDF to test PDFParser.
    items is a list of dicts:
      - {"type": "text", "text": "...", "size": float, "font": str, "y": float}
      - {"type": "table", "data": [["cell", ...], ...], "rect": (x0, y0, x1, y1)}
    """
    doc = fitz.open()
    page = doc.new_page()

    # Draw tables first so they register as table elements
    for item in items:
        if item["type"] == "table":
            rect = item["rect"]
            data = item["data"]
            
            # Draw table borders visually so PyMuPDF find_tables() can detect it
            # Number of rows and columns
            rows = len(data)
            cols = len(data[0]) if rows > 0 else 0
            
            x0, y0, x1, y1 = rect
            row_height = (y1 - y0) / rows
            col_width = (x1 - x0) / cols
            
            # Draw vertical lines
            for c in range(cols + 1):
                lx = x0 + c * col_width
                page.draw_line(fitz.Point(lx, y0), fitz.Point(lx, y1), color=(0, 0, 0), width=1)
            
            # Draw horizontal lines
            for r in range(rows + 1):
                ly = y0 + r * row_height
                page.draw_line(fitz.Point(x0, ly), fitz.Point(x1, ly), color=(0, 0, 0), width=1)
                
            # Insert cell texts
            for r in range(rows):
                for c in range(cols):
                    cell_text = str(data[r][c])
                    cx0 = x0 + c * col_width + 5
                    cy0 = y0 + r * row_height + row_height / 2 + 3
                    page.insert_text(fitz.Point(cx0, cy0), cell_text, fontsize=10, fontname="helv")

    # Draw text elements
    for item in items:
        if item["type"] == "text":
            font = item.get("font", "helv")
            size = item.get("size", 10.0)
            text = item["text"]
            y = item["y"]
            bold = item.get("bold", False)
            
            # If bold is requested, use Helvetica-Bold
            fontname = "hebo" if bold else font
            page.insert_text(fitz.Point(50, y), text, fontsize=size, fontname=fontname)

    doc.save(str(pdf_path))
    doc.close()


def test_duplicate_headings():
    """
    Test Case 1: Duplicate headings at the same level.
    The parser should append a count suffix to the second heading (e.g. 'Safety (2)')
    so that paths are unique and content hashes are distinct.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test_dup.pdf"
        
        # We write two headings with size 18.0 ("H1" size) and identical text.
        # Plus some distinct body text to verify hashes.
        items = [
            {"type": "text", "text": "Safety", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "First safety body text.", "size": 10.0, "bold": False, "y": 120},
            {"type": "text", "text": "Safety", "size": 18.0, "bold": True, "y": 200},
            {"type": "text", "text": "Second safety body text.", "size": 10.0, "bold": False, "y": 220},
        ]
        create_mock_pdf(pdf_path, items)
        
        parser = PDFParser(pdf_path)
        root = parser.parse()
        
        assert len(root.children) == 2
        
        node1 = root.children[0]
        node2 = root.children[1]
        
        assert node1.heading == "Safety"
        assert node2.heading == "Safety (2)"
        
        assert node1.heading_path == "Safety"
        assert node2.heading_path == "Safety (2)"
        
        assert node1.content_hash != node2.content_hash
        assert node1.body_text.strip() == "First safety body text."
        assert node2.body_text.strip() == "Second safety body text."


def test_embedded_tables():
    """
    Test Case 2: Tables embedded in sections.
    The parser should extract tables, format them as Markdown, attach them
    to the current node's body, and insert a table typed child node.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test_table.pdf"
        
        # H1 followed by table followed by text
        items = [
            {"type": "text", "text": "Specifications", "size": 18.0, "bold": True, "y": 100},
            {"type": "table", "data": [["Param", "Val"], ["Temp", "40C"], ["Volt", "5V"]], "rect": (50, 120, 200, 180)},
            {"type": "text", "text": "Footer note after table.", "size": 10.0, "bold": False, "y": 200},
        ]
        create_mock_pdf(pdf_path, items)
        
        parser = PDFParser(pdf_path)
        root = parser.parse()
        
        assert len(root.children) == 1
        spec_node = root.children[0]
        assert spec_node.heading == "Specifications"
        
        # Specifications node should contain the table child
        assert len(spec_node.children) == 1
        table_node = spec_node.children[0]
        assert table_node.node_type == "table"
        
        # Verify markdown content
        expected_md = "| Param | Val |\n| --- | --- |\n| Temp | 40C |\n| Volt | 5V |"
        assert table_node.body_text.strip() == expected_md
        
        # Verify that parent text has the table appended
        assert expected_md in spec_node.body_text
        assert "Footer note after table." in spec_node.body_text


def test_inconsistent_heading_levels():
    """
    Test Case 3: Inconsistent heading levels.
    When level jumps directly (e.g. from H1 directly to H3),
    the parser should normalize the level to H2 (parent level + 1)
    to keep the hierarchical tree intact.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test_inconsistent.pdf"
        
        # Define H1 (size 18), then an H3 level jump (size 12.0 bold, which would normally map to H3/H4)
        # We manually configure thresholds in tests to guarantee H1=18, H2=15, H3=12.
        items = [
            {"type": "text", "text": "Chapter 1", "size": 18.0, "bold": True, "y": 100},
            {"type": "text", "text": "Sub-sub-section", "size": 12.0, "bold": True, "y": 150},
        ]
        create_mock_pdf(pdf_path, items)
        
        parser = PDFParser(pdf_path)
        # Run analyze fonts first to populate list, then override thresholds to ensure clear classification
        parser.parse()
        parser.heading_thresholds = [17.0, 14.0, 11.0] # 18 -> H1, 12 -> H3
        
        # Parse again with overwritten thresholds
        root = parser.parse(analyze_fonts=False)
        
        assert len(root.children) == 1
        h1_node = root.children[0]
        assert h1_node.heading == "Chapter 1"
        assert h1_node.level == 1
        
        # Sub-sub-section level should be normalized to 2 (h1_node.level + 1) instead of 3
        assert len(h1_node.children) == 1
        sub_node = h1_node.children[0]
        assert sub_node.heading == "Sub-sub-section"
        assert sub_node.level == 2
