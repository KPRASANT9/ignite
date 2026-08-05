#!/bin/bash
# IGNITE Local Setup — run this after cloning the repo
# Usage: cd ignite && bash setup-local.sh

set -e

echo "=== IGNITE Local Setup ==="
echo ""

# --- Prerequisites check ---
echo "Checking prerequisites..."

command -v node >/dev/null 2>&1 || { echo "ERROR: node not found. Install Node.js 18+"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm not found. Install Node.js 18+"; exit 1; }

# Find Python 3.11+ — macOS ships 3.9 at /usr/bin/python3 which is too old.
# Try specific versions first, then fall back to python3.
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c "import sys; print(sys.version_info.minor)")
        major=$("$candidate" -c "import sys; print(sys.version_info.major)")
        if [ "$major" = "3" ] && [ "$ver" -ge 11 ]; then
            PYTHON=$(command -v "$candidate")
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ not found."
    echo "  Your system python3 is $(python3 --version 2>&1), which is too old."
    echo "  Install Python 3.12 via Homebrew:"
    echo "    brew install python@3.12"
    echo "  Then re-run this script."
    exit 1
fi

PYTHON_VERSION=$("$PYTHON" --version)
NODE_VERSION=$(node -v)
echo "  Python: $PYTHON_VERSION ($PYTHON)"
echo "  Node:   $NODE_VERSION"
echo ""

# --- Step 1: Python virtual environment ---
echo "Step 1: Setting up Python virtual environment..."

if [ -d ".venv" ]; then
    # Check if existing venv has the right Python version
    VENV_VER=$(.venv/bin/python -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
    if [ "$VENV_VER" -lt 11 ]; then
        echo "  Existing .venv uses Python 3.$VENV_VER (too old). Recreating with $PYTHON..."
        rm -rf .venv
        "$PYTHON" -m venv .venv
        echo "  Recreated .venv with $PYTHON_VERSION"
    else
        echo "  .venv already exists (Python 3.$VENV_VER)"
    fi
else
    "$PYTHON" -m venv .venv
    echo "  Created .venv with $PYTHON_VERSION"
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

# First pass: install everything except native modules that may fail
npm install --ignore-scripts --cache /tmp/npm-cache-ignite 2>&1 | grep -E "^(npm ERR|added|up to date)" || true
echo "  Base packages installed"

# Second pass: rebuild sharp (required by plasmo for icon generation)
# Install sharp for the current platform inside plasmo's nested copy
if [ -d "node_modules/plasmo/node_modules/sharp" ]; then
    echo "  Rebuilding sharp for plasmo..."
    (cd node_modules/plasmo/node_modules/sharp && npm install --platform=darwin --arch=x64 --cache /tmp/npm-cache-ignite 2>&1 | tail -1) || true
fi
# Also install sharp at top level as fallback
npm install --platform=darwin --arch=x64 sharp --cache /tmp/npm-cache-ignite 2>&1 | grep -E "^(npm ERR|added|up to date)" || true
echo "  Sharp module ready"

# --- Step 5: Build the extension ---
echo "Step 5: Building Chrome extension..."

# Ensure icon exists (plasmo requires it)
if [ ! -f "assets/icon.png" ]; then
    mkdir -p assets
    "$PYTHON" -c "
import struct, zlib
def png(size, r, g, b):
    raw = b''
    for y in range(size):
        raw += b'\x00'
        for x in range(size): raw += bytes([r, g, b, 255])
    def chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
with open('assets/icon.png', 'wb') as f: f.write(png(128, 255, 107, 0))
"
    echo "  Created extension icon"
fi

npx plasmo build 2>&1 | tail -5
if [ -f "build/chrome-mv3-prod/manifest.json" ]; then
    echo "  ✅ Extension built at: packages/extension/build/chrome-mv3-prod/"
else
    echo "  ❌ Build failed! Run manually to see errors:"
    echo "    cd packages/extension && npx plasmo build"
fi
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
