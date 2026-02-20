"""UI utilities for the Azure CLI prototype extension.

Provides Rich-based console output with:
- Progress indicators for file operations and API calls
- Claude Code-inspired styling with borders and colors
- Styled prompts with instructions
"""

from azext_prototype.ui.console import (
    Console,
    console,
    DiscoveryPrompt,
)

__all__ = [
    "Console",
    "console",
    "DiscoveryPrompt",
]
