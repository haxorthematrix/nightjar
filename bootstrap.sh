#!/usr/bin/env bash
# Nightjar bootstrap — create ./.venv and install Python dependencies.
#
# Works even on Debian/Ubuntu where the `python3-venv` package (ensurepip) is missing:
# in that case it bootstraps pip into the user site and uses `virtualenv` instead.
set -euo pipefail
cd "$(dirname "$0")"

echo "[nightjar] python: $(python3 --version)"

if [ ! -d .venv ]; then
  if python3 -c "import ensurepip" 2>/dev/null; then
    echo "[nightjar] creating .venv (stdlib venv)"
    python3 -m venv .venv
  else
    echo "[nightjar] python3-venv/ensurepip missing — bootstrapping via virtualenv"
    if ! python3 -c "import pip" 2>/dev/null; then
      curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      python3 /tmp/get-pip.py --user --break-system-packages
    fi
    python3 -m pip install --user --break-system-packages virtualenv
    python3 -m virtualenv .venv
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r requirements.txt

echo
echo "[nightjar] done. Start with:"
echo "    source .venv/bin/activate && python run.py"
echo "then open http://127.0.0.1:8008"
