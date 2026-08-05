#!/bin/bash
# IGNITE Local Setup — run this after cloning the repo
# Usage: cd ignite && bash setup-local.sh

set -e

echo "=== IGNITE Local Setup ==="
echo ""

# --- Prerequisites check ---
echo "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found. Install Python 3.11+"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: node not found. Install Node.js 18+"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm not found. Install Node.js 18+"; exit 1; }

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
NODE_VERSION=$(node -v)
echo "  Python: $PYTHON_VERSION"
echo "  Node:   $NODE_VERSION"
echo ""

# --- Step 1: Python virtual environment ---
echo "Step 1: Setting up Python virtual environment..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  Created .venv"
else
    echo "  .venv already exists"
fi

# --- Step 2: Install Python packages ---
echo "Step 2: Installing Python packages..."

.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e packages/trace-sdk
.venv/bin/pip install --quiet -e packages/parser
.venv/bin/pip install --quiet -e packages/bridge
echo "  Installed: ignite-trace, ignite-parser, ignite-bridge"

# --- Step 3: Verify Python setup ---
echo "Step 3: Verifying Python setup..."

.venv/bin/python -c "
from ignite_bridge.app import create_app
from ignite_parser import parse_trace, detect_spikes
from ignite_trace import Span
print('  All Python packages OK')
"

# --- Step 4: Install extension dependencies ---
echo "Step 4: Installing extension (Node.js) dependencies..."

cd packages/extension
npm install --ignore-scripts --cache /tmp/npm-cache-ignite 2>/dev/null
echo "  Node modules installed"

# --- Step 5: Build the extension ---
echo "Step 5: Building Chrome extension..."

npx plasmo build 2>/dev/null
echo "  Extension built at: packages/extension/build/chrome-mv3-prod/"
cd ../..

# --- Step 6: Run Python tests ---
echo "Step 6: Running Python tests..."

TEST_RESULT=$(.venv/bin/python -m pytest packages/parser/tests/ packages/bridge/tests/ packages/trace-sdk/tests/ -q --tb=no 2>&1 | tail -1)
echo "  $TEST_RESULT"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the bridge:"
echo "  .venv/bin/python -m ignite_bridge"
echo ""
echo "To load in Chrome:"
echo "  1. Open chrome://extensions/"
echo "  2. Enable Developer mode"
echo "  3. Load unpacked → packages/extension/build/chrome-mv3-prod"
echo ""
echo "Then visit any website — traces flow automatically."
