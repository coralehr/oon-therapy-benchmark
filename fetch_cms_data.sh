#!/usr/bin/env bash
# Fetch the public-domain CMS source files the pipeline needs.
# Re-run quarterly when CMS publishes a new RVU release (RVU26A, RVU26B, ...).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/raw
cd data/raw

RELEASE_URL="${1:-https://www.cms.gov/files/zip/rvu26a.zip}"
echo "Downloading $RELEASE_URL ..."
curl -fsSL --max-time 120 -o rvu.zip "$RELEASE_URL"

echo "Extracting RVU + GPCI CSVs ..."
unzip -o -q rvu.zip "PPRRVU2026_Jan_nonQPP.csv" "GPCI2026.csv" || \
  unzip -o -q rvu.zip "PPRRVU*nonQPP.csv" "GPCI*.csv"

echo "Done. Source files in data/raw/"
ls -1 *.csv
