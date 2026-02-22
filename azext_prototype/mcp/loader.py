"""Load MCP handlers from Python files.

Follows the same pattern as agents/loader.py: each handler file must
either set ``MCP_HANDLER_CLASS`` or define exactly one MCPHandler
subclass.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from azext_prototype.mcp.base import MCPHandler, MCPHandlerConfig

logger = logging.getLogger(__name__)


def load_mcp_handler(
    file_path: str,
    config: MCPHandlerConfig,
    **kwargs: Any,
) -> MCPHandler:
    """Load a handler from a Python file.

    The file must set ``MCP_HANDLER_CLASS`` or define exactly one
    ``MCPHandler`` subclass.

    Args:
        file_path: Path to the Python handler file.
        config: Handler configuration from prototype.yaml.
        **kwargs: Additional keyword args passed to the handler constructor
            (e.g. console, project_config).

    Returns:
        Instantiated MCPHandler subclass.

    Raises:
        ValueError: If file is invalid or no handler class is found.
    """
    path = Path(file_path)

    if not path.exists():
        raise ValueError(f"Handler file not found: {file_path}")

    if path.suffix != ".py":
        raise ValueError(f"Expected .py file, got: {path.suffix}")

    try:
        spec = importlib.util.spec_from_file_location(
            f"mcp_handler_{path.stem}",
            str(path),
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load module spec from {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to load MCP handler from {file_path}: {exc}") from exc

    # Check for explicit MCP_HANDLER_CLASS
    if hasattr(module, "MCP_HANDLER_CLASS"):
        handler_cls = module.MCP_HANDLER_CLASS
        if isinstance(handler_cls, type) and issubclass(handler_cls, MCPHandler):
            return handler_cls(config, **kwargs)
        raise ValueError(f"MCP_HANDLER_CLASS in {file_path} must be an MCPHandler subclass.")

    # Auto-discover MCPHandler subclass
    handler_classes = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and issubclass(attr, MCPHandler) and attr is not MCPHandler:
            handler_classes.append(attr)

    if len(handler_classes) == 0:
        raise ValueError(f"No MCPHandler subclass found in {file_path}. " "Define a class that extends MCPHandler.")

    if len(handler_classes) > 1:
        raise ValueError(
            f"Multiple MCPHandler subclasses found in {file_path}: "
            f"{[c.__name__ for c in handler_classes]}. "
            "Set MCP_HANDLER_CLASS to specify which one to use."
        )

    return handler_classes[0](config, **kwargs)


def load_handlers_from_directory(
    directory: str,
    configs: dict[str, MCPHandlerConfig],
    **kwargs: Any,
) -> list[MCPHandler]:
    """Scan a directory for handler .py files and load them.

    Each file is matched to a config by handler name. Files whose stem
    matches a config name (with ``_handler`` suffix stripped) are loaded.
    Files without a matching config are skipped with a warning.

    Args:
        directory: Path to directory containing handler .py files.
        configs: Map of handler name -> MCPHandlerConfig.
        **kwargs: Additional keyword args passed to handler constructors.

    Returns:
        List of loaded handlers.
    """
    path = Path(directory)
    if not path.is_dir():
        logger.debug("MCP handler directory does not exist: %s", directory)
        return []

    handlers: list[MCPHandler] = []

    for file_path in sorted(path.iterdir()):
        if file_path.suffix != ".py" or file_path.name.startswith("_"):
            continue

        # Derive handler name from filename: strip _handler suffix
        stem = file_path.stem
        handler_name = stem.removesuffix("_handler")

        config = configs.get(handler_name)
        if config is None:
            logger.debug(
                "No config for MCP handler '%s' (from %s), skipping",
                handler_name,
                file_path.name,
            )
            continue

        try:
            handler = load_mcp_handler(str(file_path), config, **kwargs)
            handlers.append(handler)
            logger.info("Loaded MCP handler '%s' from %s", handler.name, file_path)
        except (ValueError, Exception) as exc:
            logger.warning("Failed to load MCP handler from %s: %s", file_path, exc)

    logger.info("Loaded %d MCP handlers from %s", len(handlers), directory)
    return handlers
