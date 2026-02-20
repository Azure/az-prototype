"""Extract file blocks from AI-generated markdown responses.

AI agents commonly embed generated files inside fenced code blocks that use
the filename (with optional language hint) as the info-string:

    ```main.tf
    resource "azurerm_resource_group" "rg" { ... }
    ```

    ```python:src/app.py
    # application code
    ```

This module provides a robust, reusable parser that handles:
- Standard triple-backtick ``filename.ext`` markers
- Language-prefixed names (``hcl:main.tf``)
- Nested directory paths (``infra/modules/network.tf``)
- Unclosed trailing blocks (treated as complete)
- Blocks without filenames (skipped)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Matches the opening of a fenced code block that looks like a filename.
# Captures an optional language prefix (e.g. "hcl:") and the path.
# A valid path must contain a "." (extension) or a "/" (directory separator).
_FENCE_RE = re.compile(
    r"^(`{3,})"            # 1: opening backtick fence (3+)
    r"\s*"
    r"(?:[a-zA-Z0-9_+-]+:)?"   # optional language: prefix (non-capturing)
    r"\s*"
    r"([\w./-]+)"          # 2: potential file path
    r"\s*$"
)


def parse_file_blocks(content: str) -> dict[str, str]:
    """Parse file blocks from AI-generated markdown.

    Parameters
    ----------
    content:
        Raw markdown text that may contain fenced code blocks whose
        info-string is a filename or path.

    Returns
    -------
    dict[str, str]:
        Mapping of ``filename -> content``.  Filenames preserve the
        relative path exactly as written by the AI (e.g.
        ``"infra/main.tf"``).  An empty dict is returned when no
        file blocks are detected.

    Examples
    --------
    >>> text = '''Here is the code:
    ... ```main.tf
    ... resource "azurerm_resource_group" "rg" {}
    ... ```
    ... '''
    >>> parse_file_blocks(text)
    {'main.tf': 'resource "azurerm_resource_group" "rg" {}'}
    """
    files: dict[str, str] = {}
    lines = content.split("\n")

    current_file: str | None = None
    current_content: list[str] = []
    fence_len: int = 0  # length of opening backtick fence

    for line in lines:
        stripped = line.rstrip()

        # --- Try to match a closing fence ---
        if current_file is not None:
            # A closing fence must have at least as many backticks as the
            # opening fence and nothing else on the line.
            if stripped.startswith("`" * fence_len) and stripped == "`" * len(stripped):
                files[current_file] = "\n".join(current_content)
                current_file = None
                current_content = []
                fence_len = 0
                continue

            # We're inside a block: accumulate
            current_content.append(line)
            continue

        # --- Try to match an opening fence with a filename ---
        m = _FENCE_RE.match(stripped)
        if m:
            candidate = m.group(2)
            # Require at least one dot (extension) or slash (directory path)
            if "." in candidate or "/" in candidate:
                # Flush any previous unclosed block (shouldn't happen normally)
                if current_file and current_content:
                    files[current_file] = "\n".join(current_content)

                fence_len = len(m.group(1))
                current_file = candidate
                current_content = []
                continue

    # Handle unclosed trailing block
    if current_file and current_content:
        files[current_file] = "\n".join(current_content)
        logger.debug("Flushing unclosed file block: %s", current_file)

    return files


def write_parsed_files(
    files: dict[str, str],
    output_dir: str | Path,
    *,
    verbose: bool = True,
    label: str | None = None,
    print_fn: Callable[..., Any] | None = None,
) -> list[Path]:
    """Write parsed file blocks to disk.

    Parameters
    ----------
    files:
        Mapping of relative filename → content as returned by
        :func:`parse_file_blocks`.
    output_dir:
        Root directory under which files will be written.
    verbose:
        When *True*, print the path of each file written.
    label:
        Optional prefix label shown in verbose output
        (e.g. ``"infra"`` → ``"infra/main.tf"``).
    print_fn:
        Optional callable for verbose output.  Defaults to ``print``.

    Returns
    -------
    list[Path]:
        Absolute paths of files that were written.
    """
    _print = print_fn or print
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for filename, content in files.items():
        file_path = output_path / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        written.append(file_path)

        if verbose:
            display = f"{label}/{filename}" if label else filename
            _print(f"   {display}")

    return written
