#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec /home/azer/CodeReviewQA/.venv/bin/python -m uvicorn api:app --reload --port 8000