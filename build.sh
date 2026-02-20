#!/usr/bin/env bash
set -euo pipefail

echo "========================================"
echo " Azure CLI Extension - Build & Install"
echo "========================================"
echo

# Check Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 is not installed or not in PATH."
    echo "Install with: sudo apt install python3 python3-pip  (Debian/Ubuntu)"
    echo "         or:  brew install python3                   (macOS)"
    exit 1
fi

PYTHON=python3

# Ensure build tool is installed
echo "[1/3] Ensuring build tools are installed..."
$PYTHON -m pip install --upgrade build setuptools wheel --quiet

# Clean previous builds
echo "[2/3] Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info

# Build the wheel
echo "[3/3] Building wheel..."
$PYTHON -m build --wheel
if [ $? -ne 0 ]; then
    echo "ERROR: Build failed."
    exit 1
fi

WHL_FILE=$(ls dist/az_prototype-*.whl 2>/dev/null | head -n 1)
if [ -z "$WHL_FILE" ]; then
    echo "ERROR: No .whl file found in dist/"
    exit 1
fi

echo
echo "========================================"
echo " Build complete!"
echo " Wheel: $WHL_FILE"
echo ""
echo " Install with:"
echo "   az extension remove --name prototype 2>/dev/null; az extension add --source $WHL_FILE --yes"
echo "========================================"
