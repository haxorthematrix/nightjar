#!/usr/bin/env bash
# Reset Nightjar back to a clean state: delete the local database and all captured imagery.
# A fresh, empty database is created automatically on the next start.
#
# Usage:  ./scripts/reset_db.sh        (stop the server first)
set -euo pipefail
cd "$(dirname "$0")/.."

rm -f data/nightjar.db data/nightjar.db-wal data/nightjar.db-shm
rm -f data/captures/*.jpg data/captures/*.png 2>/dev/null || true

echo "[nightjar] database and captured images cleared — a fresh DB is created on next start."
