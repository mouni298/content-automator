#!/usr/bin/env bash
# Daily cron entry point. Generates one video and sends it to Telegram for review.
# The review bot (src.review) must be running separately to handle Approve/Publish.
set -euo pipefail

cd "$(dirname "$0")/.."

# activate venv if present
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "=== content-automator run: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
python -m src.main
