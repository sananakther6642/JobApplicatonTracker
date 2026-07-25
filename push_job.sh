#!/bin/bash
# Usage: ./push_job.sh job.json
# Edit job.json, then run this script.

FILE="${1:-job.json}"

if [ ! -f "$FILE" ]; then
  echo "File not found: $FILE"
  exit 1
fi

curl -s -X POST http://localhost:5050/api/job \
  -H "Content-Type: application/json" \
  -d @"$FILE" | python3 -m json.tool
