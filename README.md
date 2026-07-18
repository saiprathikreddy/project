# CT-200 QA Backend

FastAPI backend that parses CT-200 Blood Pressure Monitor PDF manuals into a versioned hierarchical tree, and generates QA test cases using Gemini.

---

## Setup and Installation

### 1. Prerequisites
- Python 3.10+
- SQLite (included with Python)
- MongoDB running locally (default: `mongodb://localhost:27017`)
  *Note: If MongoDB is offline, the application automatically triggers a transparent JSON file fallback (`data/mongodb_fallback.json`) for LLM output storage.*

### 2. Install Dependencies
```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Configuration & Environment Variables

Create a `.env` file in the project root:
```bash
cp .env.example .env
```

Configure the following variables in `.env`:
- `DATABASE_URL`: SQLite connection string (default: `sqlite:///./data/ct200.db`).
- `MONGODB_URI`: MongoDB connection string (default: `mongodb://localhost:27017`).
- `GEMINI_API_KEY`: Your Google AI Studio API key.
- `GEMINI_MODEL`: Gemini model to call (default: `gemini-3.5-flash`).

---

## Running the Application

Start the FastAPI development server:
```bash
uvicorn app.main:app --reload
```
Open [http://localhost:8000/docs](http://localhost:8000/docs) to access the interactive Swagger API documentation.

---

## Running Tests

Execute the automated test suite:
```bash
.venv\Scripts\python -m pytest -v
```

---

## Triggering the V1 → V2 Re-Ingestion Flow

The version matching and re-ingestion pipeline is triggered by uploading a new version of the PDF manual to the `/ingest` API using the **same `device_model`** identifier.

### Method 1: Using the Automated PowerShell Script (Windows)
Run the end-to-end demonstration script:
```powershell
.\demo.ps1
```
This script automates:
1. Ingesting `ct200_v1.pdf` (Version 1).
2. Creating a selection basket.
3. Generating test cases.
4. Ingesting `ct200_v2.pdf` (Version 2).
5. Fetching generation status and checking for staleness updates.

### Method 2: Manual Trigger via `curl`
1. **Ingest Version 1:**
   ```bash
   curl -X POST "http://localhost:8000/ingest" \
     -F "title=CT-200 Blood Pressure Monitor Manual" \
     -F "device_model=CT-200" \
     -F "description=Version 1 Manual" \
     -F "file=@data/ct200_v1.pdf"
   ```
2. **Ingest Version 2 (Re-Ingestion):**
   Simply upload the updated PDF using the same `device_model` string (`CT-200`):
   ```bash
   curl -X POST "http://localhost:8000/ingest" \
     -F "title=CT-200 Blood Pressure Monitor Manual" \
     -F "device_model=CT-200" \
     -F "description=Version 2 Manual" \
     -F "file=@data/ct200_v2.pdf"
   ```
   The backend automatically:
   - Detects the existing document for `CT-200`.
   - Increments the version number to `2`.
   - Compares the heading paths of version 2 with version 1.
   - Computes diffs and links matching nodes to their prior version.
