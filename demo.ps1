# demo.ps1
# End-to-End Demonstration Script for CT-200 QA Backend
# OS: Windows (PowerShell)
#
# This script demonstrates the full workflow:
# 1. Ingest Version 1 PDF
# 2. Browse sections to find node IDs
# 3. Create a version-pinned Selection
# 4. Generate QA test cases (saved in MongoDB, indexed in SQLite)
# 5. Ingest Version 2 PDF (with changes)
# 6. Retrieve test cases and show real-time staleness status
#
# Prerequisites:
# - FastAPI server must be running at http://localhost:8000
# - MongoDB must be running at mongodb://localhost:27017
# - PDF files must exist in data/ct200_v1.pdf and data/ct200_v2.pdf

$API_BASE = "http://localhost:8000"

# Colors for output
function Write-Header($msg) {
    Write-Host "`n==== $msg ====" -ForegroundColor Cyan
}

function Write-Success($msg) {
    Write-Host "Success: $msg" -ForegroundColor Green
}

function Write-ErrorMsg($msg) {
    Write-Host "Error: $msg" -ForegroundColor Red
}

# Verify server is online
try {
    $health = Invoke-RestMethod -Uri "$API_BASE/health" -Method Get
    if ($health.status -ne "ok") {
        Write-ErrorMsg "FastAPI server is not responding correctly."
        exit
    }
} catch {
    Write-ErrorMsg "FastAPI server is offline. Please run 'uvicorn app.main:app --reload' first."
    exit
}

# Verify V1 and V2 PDFs exist
$v1_path = "data/ct200_v1.pdf"
$v2_path = "data/ct200_v2.pdf"

if (-not (Test-Path $v1_path)) {
    Write-ErrorMsg "V1 PDF manual not found at '$v1_path'. Please place the files in data/ directory."
    exit
}
if (-not (Test-Path $v2_path)) {
    Write-ErrorMsg "V2 PDF manual not found at '$v2_path'. Please place the files in data/ directory."
    exit
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. Ingest V1 PDF
# ─────────────────────────────────────────────────────────────────────────────
Write-Header "Step 1: Ingesting CT-200 Version 1 PDF manual"

# We must use multipart/form-data for files upload in PowerShell
$v1_bytes = [System.IO.File]::ReadAllBytes((Resolve-Path $v1_path))
$LF = "`r`n"
$boundary = [System.Guid]::NewGuid().ToString()

$body = (
    "--$boundary$LF" +
    "Content-Disposition: form-data; name=`"title`"$LF$LF" +
    "CT-200 Blood Pressure Monitor Manual$LF" +
    "--$boundary$LF" +
    "Content-Disposition: form-data; name=`"device_model`"$LF$LF" +
    "CT-200$LF" +
    "--$boundary$LF" +
    "Content-Disposition: form-data; name=`"description`"$LF$LF" +
    "Initial released draft of product specification$LF" +
    "--$boundary$LF" +
    "Content-Disposition: form-data; name=`"file`"; filename=`"ct200_v1.pdf`"$LF" +
    "Content-Type: application/pdf$LF$LF"
)

$header = @{
    "Content-Type" = "multipart/form-data; boundary=$boundary"
}

# Pack body and file bytes
$encoding = [System.Text.Encoding]::GetEncoding('iso-8859-1')
$bodyBytes = $encoding.GetBytes($body)
$endBytes = $encoding.GetBytes("$LF--$boundary--$LF")

$totalBytes = New-Object Byte[] ($bodyBytes.Length + $v1_bytes.Length + $endBytes.Length)
[System.Buffer]::BlockCopy($bodyBytes, 0, $totalBytes, 0, $bodyBytes.Length)
[System.Buffer]::BlockCopy($v1_bytes, 0, $totalBytes, $bodyBytes.Length, $v1_bytes.Length)
[System.Buffer]::BlockCopy($endBytes, 0, $totalBytes, ($bodyBytes.Length + $v1_bytes.Length), $endBytes.Length)

$ingest_v1_res = Invoke-RestMethod -Uri "$API_BASE/ingest" -Method Post -Headers $header -Body $totalBytes
Write-Success "Ingested version $($ingest_v1_res.version_number) successfully."
$ingest_v1_res | ConvertTo-Json

# ─────────────────────────────────────────────────────────────────────────────
# 2. Browse Sections
# ─────────────────────────────────────────────────────────────────────────────
Write-Header "Step 2: Browsing sections in V1 to locate H1 node IDs"
$sections = Invoke-RestMethod -Uri "$API_BASE/sections?device_model=CT-200&version_number=1&level=1" -Method Get
$sections | Format-Table id, heading, level, node_type

# Find some nodes to create a selection basket
$node_ids = $sections | Select-Object -ExpandProperty id
if ($node_ids.Count -eq 0) {
    Write-ErrorMsg "No sections found. Ingestion failed to parse hierarchy."
    exit
}

# We pick up to 2 nodes
$selected_node_ids = $node_ids[0..1]
Write-Host "Selected Node IDs for QA Selection: $selected_node_ids" -ForegroundColor Cyan

# ─────────────────────────────────────────────────────────────────────────────
# 3. Create Selection
# ─────────────────────────────────────────────────────────────────────────────
Write-Header "Step 3: Creating a version-pinned selection basket"
$selection_body = @{
    name = "Key Safety & Operations Basket"
    device_model = "CT-200"
    version_number = 1
    node_ids = $selected_node_ids
    description = "Test suite covering safety protocols and base calibration"
} | ConvertTo-Json

$selection_res = Invoke-RestMethod -Uri "$API_BASE/selections" -Method Post -ContentType "application/json" -Body $selection_body
$selection_id = $selection_res.id
Write-Success "Created Selection ID: $selection_id"
$selection_res | ConvertTo-Json

# ─────────────────────────────────────────────────────────────────────────────
# 4. Generate QA Test Cases
# ─────────────────────────────────────────────────────────────────────────────
Write-Header "Step 4: Calling LLM to generate QA test cases (Gemini -> MongoDB)"
$gen_body = @{
    selection_id = $selection_id
} | ConvertTo-Json

$gen_res = Invoke-RestMethod -Uri "$API_BASE/generations" -Method Post -ContentType "application/json" -Body $gen_body
$generation_id = $gen_res.id
Write-Success "Generated test cases successfully (Cached status: $($gen_res.is_cached))"
$gen_res | ConvertTo-Json

# ─────────────────────────────────────────────────────────────────────────────
# 5. Ingest V2 PDF
# ─────────────────────────────────────────────────────────────────────────────
Write-Header "Step 5: Ingesting CT-200 Version 2 PDF manual (with updates)"

$v2_bytes = [System.IO.File]::ReadAllBytes((Resolve-Path $v2_path))
$boundary_v2 = [System.Guid]::NewGuid().ToString()

$body_v2 = (
    "--$boundary_v2$LF" +
    "Content-Disposition: form-data; name=`"title`"$LF$LF" +
    "CT-200 Blood Pressure Monitor Manual$LF" +
    "--$boundary_v2$LF" +
    "Content-Disposition: form-data; name=`"device_model`"$LF$LF" +
    "CT-200$LF" +
    "--$boundary_v2$LF" +
    "Content-Disposition: form-data; name=`"description`"$LF$LF" +
    "Second revision manual with updated safety thresholds$LF" +
    "--$boundary_v2$LF" +
    "Content-Disposition: form-data; name=`"file`"; filename=`"ct200_v2.pdf`"$LF" +
    "Content-Type: application/pdf$LF$LF"
)

$header_v2 = @{
    "Content-Type" = "multipart/form-data; boundary=$boundary_v2"
}

$bodyBytes_v2 = $encoding.GetBytes($body_v2)
$endBytes_v2 = $encoding.GetBytes("$LF--$boundary_v2--$LF")

$totalBytes_v2 = New-Object Byte[] ($bodyBytes_v2.Length + $v2_bytes.Length + $endBytes_v2.Length)
[System.Buffer]::BlockCopy($bodyBytes_v2, 0, $totalBytes_v2, 0, $bodyBytes_v2.Length)
[System.Buffer]::BlockCopy($v2_bytes, 0, $totalBytes_v2, $bodyBytes_v2.Length, $v2_bytes.Length)
[System.Buffer]::BlockCopy($endBytes_v2, 0, $totalBytes_v2, ($bodyBytes_v2.Length + $v2_bytes.Length), $endBytes_v2.Length)

$ingest_v2_res = Invoke-RestMethod -Uri "$API_BASE/ingest" -Method Post -Headers $header_v2 -Body $totalBytes_v2
Write-Success "Ingested version $($ingest_v2_res.version_number) successfully."
$ingest_v2_res | ConvertTo-Json

# ─────────────────────────────────────────────────────────────────────────────
# 6. Retrieve Generations with Staleness check
# ─────────────────────────────────────────────────────────────────────────────
Write-Header "Step 6: Retrieving test cases and checking for Staleness"
$retrieve_res = Invoke-RestMethod -Uri "$API_BASE/generations/$generation_id" -Method Get

Write-Success "Retrieved Generation ID: $($retrieve_res.id)"
Write-Host "Staleness Status: " -NoNewline
if ($retrieve_res.staleness.is_stale) {
    Write-Host "STALE (New manual version changes detected!)" -ForegroundColor Yellow
} else {
    Write-Host "FRESH (Identical to latest manual version)" -ForegroundColor Green
}

$retrieve_res | ConvertTo-Json
