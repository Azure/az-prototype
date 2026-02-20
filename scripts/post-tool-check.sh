#!/usr/bin/env bash
# postToolUse hook — runs Python type checking and linting after file modifications.
# Receives JSON input on stdin with tool execution details.
set -euo pipefail

INPUT=$(cat)

# Extract tool name from JSON input
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('toolName', ''))" 2>/dev/null || echo "")

# Only run on file-modifying tools
case "$TOOL_NAME" in
    create_file|replace_string_in_file|multi_replace_string_in_file|edit_notebook_file) ;;
    *) exit 0 ;;
esac

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
fi

ERRORS=0

# --- Linting with ruff ---
if command -v ruff &> /dev/null; then
    echo "Running ruff linter..."
    if ! ruff check azext_prototype/ tests/ --no-fix 2>/dev/null; then
        echo "WARNING: Ruff found linting issues."
        ERRORS=1
    else
        echo "OK: Ruff clean."
    fi
fi

# --- Type checking with mypy ---
if command -v mypy &> /dev/null; then
    echo "Running mypy type checker..."
    if ! mypy azext_prototype/ --ignore-missing-imports --no-error-summary 2>/dev/null; then
        echo "WARNING: mypy found type issues."
        ERRORS=1
    else
        echo "OK: mypy clean."
    fi
fi

# --- Type checking with pyright (Pylance) ---
if command -v pyright &> /dev/null; then
    echo "Running pyright type checker..."
    if ! pyright azext_prototype/ 2>/dev/null; then
        echo "WARNING: pyright found type issues."
        ERRORS=1
    else
        echo "OK: pyright clean."
    fi
fi

if [ $ERRORS -ne 0 ]; then
    echo "postToolUse: issues found. Please review above."
    exit 1
fi

echo "All checks passed."
exit 0
