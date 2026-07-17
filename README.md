# CT-200 QA Backend

FastAPI backend that parses CT-200 Blood Pressure Monitor PDF manuals into a versioned
hierarchical tree, and generates QA test cases using Gemini.

## Quick start

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env          # fill in GEMINI_API_KEY
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the interactive API.

## Build steps (commit after each)

| Step | Area | What |
|------|------|------|
| 1 | scaffold | Project structure, requirements, config |
| 2 | models | SQLAlchemy models: Document, DocumentVersion, Node, Selection, SelectionNode, Generation |
| 3 | parser | pymupdf PDF parser — heading hierarchy, body text, content hash |
| 4 | tests | 3 unit tests for parser edge cases |
| 5 | ingest | POST /ingest — parse + persist + version |
| 6 | browse | GET /sections, /nodes/{id}, /search, /nodes/{id}/diff |
| 7 | selection | POST /selections, GET /selections/{id} |
| 8 | llm | Gemini client, structured output, retry, dedup policy |
| 9 | generate | POST /generations |
| 10 | mongo | MongoDB persistence for LLM output |
| 11 | staleness | Staleness checker service |
| 12 | retrieve | GET /generations/{id}, GET /nodes/{id}/generations |
| 13 | demo | End-to-end curl demo script |

## Design decisions

### PDF parser: PyMuPDF over pdfplumber
- `page.get_text("dict")` returns span-level flags (bold, italic, font name, size) in one call — no character grouping needed
- ~10× faster on large manuals; relevant when re-ingesting on every version bump
- `find_tables()` (fitz 1.23+) handles bordered tables better on CT-200's spec tables
- Trade-off: AGPL license applies to the library; swap to pdfplumber (MIT) if open-sourcing the project

### Node content hash
SHA-256 of `heading + "\n" + body_text` (both stripped). This means:
- Whitespace-only edits are **detected** as changes (deliberate — medical device text precision matters)
- Pure structural moves (node reordered but text identical) are **not** detected — known limitation documented in `versioner.py`

### Versioning / node matching strategy
Nodes are matched across versions by **heading path** (root → leaf heading sequence joined by `" > "`). Same path + same hash = unchanged. Same path + different hash = changed. New path = added. Missing path = deleted.

**Known break case:** If a section is renamed AND its content changes simultaneously, the old node appears as deleted and the new node as added — the connection is lost. A more robust strategy (e.g., edit-distance on body text) is noted as a future improvement.

### Deduplication policy for LLM generations
Defined in `llm_client.py`. A generation is considered a duplicate if, within the last 24 hours, an existing generation exists for the same `selection_id` AND the set of `content_hashes` of the selected nodes is identical. Duplicate requests return the cached generation with a `cached: true` flag in the response, and no new Gemini call is made.

## Running tests

```bash
pytest -v
```
