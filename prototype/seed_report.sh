#!/usr/bin/env bash
set -euo pipefail
cd /home/azer/CodeReviewQA/prototype
export PATH="$HOME/.local/bin:$PATH"
exec /home/azer/CodeReviewQA/.venv/bin/python seed_report.py