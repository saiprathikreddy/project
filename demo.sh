#!/bin/bash
# demo.sh
# End-to-End Demonstration Script for CT-200 QA Backend
# OS: macOS/Linux/Git Bash
#
# This script demonstrates the full workflow:
# 1. Ingest Version 1 PDF
# 2. Browse sections to find node IDs
# 3. Create a version-pinned Selection
# 4. Generate QA test cases (saved in MongoDB, indexed in SQLite)
# 5. Ingest Version 2 PDF (with changes)
# 6. Retrieve test cases and show real-time staleness status

API_BASE="http://localhost:8000"

# Colors for output
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

write_header() {
    echo -e "\n${CYAN}==== $1 ====${NC}"
}

write_success() {
    echo -e "${GREEN}Success: $1${NC}"
}

write_error() {
    echo -e "${RED}Error: $1${NC}"
}

# Verify server is online
if ! curl -s "$API_BASE/health" | grep -q "ok"; then
    write_error "FastAPI server is offline. Please run 'uvicorn app.main:app --reload' first."
    exit 1
fi

v1_path="data/ct200_v1.pdf"
v2_path="data/ct200_v2.pdf"

if [ ! -f "$v1_path" ]; then
    write_error "V1 PDF manual not found at '$v1_path'. Please place the files in data/ directory."
    exit 1
fi
if [ ! -f "$v2_path" ]; then
    write_error "V2 PDF manual not found at '$v2_path'. Please place the files in data/ directory."
    exit 1
fi

# 1. Ingest V1 PDF
write_header "Step 1: Ingesting CT-200 Version 1 PDF manual"
ingest_v1_res=$(curl -s -X POST "$API_BASE/ingest" \
  -F "title=CT-200 Blood Pressure Monitor Manual" \
  -F "device_model=CT-200" \
  -F "description=Initial released draft of product specification" \
  -F "file=@$v1_path")

echo "$ingest_v1_res" | python -m json.tool 2>/dev/null || echo "$ingest_v1_res"

version_number=$(python -c "import sys, json; print(json.load(sys.stdin).get('version_number', ''))" <<< "$ingest_v1_res")
if [ -z "$version_number" ] || [ "$version_number" == "None" ]; then
    write_error "Ingestion failed."
    exit 1
fi
write_success "Ingested version $version_number successfully."

# 2. Browse Sections
write_header "Step 2: Browsing sections in V1 to locate H1 node IDs"
sections_res=$(curl -s -G "$API_BASE/sections" \
  --data-urlencode "device_model=CT-200" \
  --data-urlencode "version_number=1" \
  --data-urlencode "level=1")

python -c "import sys, json; data=json.load(sys.stdin); [print(f\"ID: {n['id']} | Heading: {n['heading']} | Level: {n['level']}\") for n in data]" <<< "$sections_res"

# Extract the first two node IDs
node_ids=($(python -c "import sys, json; data=json.load(sys.stdin); print(' '.join(str(n['id']) for n in data))" <<< "$sections_res"))
if [ ${#node_ids[@]} -eq 0 ]; then
    write_error "No sections found. Ingestion failed to parse hierarchy."
    exit 1
fi

selected_nodes="[${node_ids[0]}, ${node_ids[1]}]"
echo -e "${CYAN}Selected Node IDs for QA Selection: $selected_nodes${NC}"

# 3. Create Selection
write_header "Step 3: Creating a version-pinned selection basket"
selection_res=$(curl -s -X POST "$API_BASE/selections" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"Key Safety & Operations Basket\", \"device_model\": \"CT-200\", \"version_number\": 1, \"node_ids\": $selected_nodes, \"description\": \"Test suite covering safety protocols\"}")

echo "$selection_res" | python -m json.tool 2>/dev/null || echo "$selection_res"
selection_id=$(python -c "import sys, json; print(json.load(sys.stdin).get('id', ''))" <<< "$selection_res")
write_success "Created Selection ID: $selection_id"

# 4. Generate QA Test Cases
write_header "Step 4: Calling LLM to generate QA test cases (Gemini -> MongoDB)"
gen_res=$(curl -s -X POST "$API_BASE/generations" \
  -H "Content-Type: application/json" \
  -d "{\"selection_id\": $selection_id}")

echo "$gen_res" | python -m json.tool 2>/dev/null || echo "$gen_res"
generation_id=$(python -c "import sys, json; print(json.load(sys.stdin).get('id', ''))" <<< "$gen_res")
is_cached=$(python -c "import sys, json; print(json.load(sys.stdin).get('is_cached', ''))" <<< "$gen_res")
write_success "Generated test cases successfully (Cached status: $is_cached)"

# 5. Ingest V2 PDF
write_header "Step 5: Ingesting CT-200 Version 2 PDF manual (with updates)"
ingest_v2_res=$(curl -s -X POST "$API_BASE/ingest" \
  -F "title=CT-200 Blood Pressure Monitor Manual" \
  -F "device_model=CT-200" \
  -F "description=Second revision manual with updated safety thresholds" \
  -F "file=@$v2_path")

echo "$ingest_v2_res" | python -m json.tool 2>/dev/null || echo "$ingest_v2_res"
write_success "Ingested version 2 successfully."

# 6. Retrieve Generations with Staleness check
write_header "Step 6: Retrieving test cases and checking for Staleness"
retrieve_res=$(curl -s -X GET "$API_BASE/generations/$generation_id")

echo "$retrieve_res" | python -m json.tool 2>/dev/null || echo "$retrieve_res"

is_stale=$(python -c "import sys, json; data=json.load(sys.stdin); print(str(data.get('staleness', {}).get('is_stale', '')).lower())" <<< "$retrieve_res")
echo -ne "${GREEN}Retrieved Generation ID: $generation_id${NC}\n"
echo -ne "${CYAN}Staleness Status: ${NC}"
if [ "$is_stale" == "true" ]; then
    echo -e "${YELLOW}STALE (New manual version changes detected!)${NC}"
else
    echo -e "${GREEN}FRESH (Identical to latest manual version)${NC}"
fi
