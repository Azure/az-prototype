"""Project template registry and loader.

Templates are YAML manifests (``*.template.yaml``) that describe a
starter project topology: which Azure services to use, how they connect,
default configuration values, and a requirements blurb that seeds the
design stage.

Built-in templates live under ``azext_prototype/templates/workloads/``.
Users can add custom templates to ``.prototype/templates/`` in their
project directory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TemplateService:
    """An Azure service declared by a template."""

    name: str
    type: str  # e.g. container-apps, sql-database, key-vault
    tier: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectTemplate:
    """A loaded project template."""

    name: str
    display_name: str
    description: str
    category: str  # web-app, data-pipeline, ai-app, microservices, serverless
    services: list[TemplateService] = field(default_factory=list)
    iac_defaults: dict[str, Any] = field(default_factory=dict)
    requirements: str = ""
    tags: list[str] = field(default_factory=list)

    def service_names(self) -> list[str]:
        """Return the service type names (for policy resolution)."""
        return [s.type for s in self.services]


class TemplateRegistry:
    """Discovers and loads project templates."""

    def __init__(self) -> None:
        self._templates: dict[str, ProjectTemplate] = {}
        self._loaded = False

    def load(self, directories: list[Path] | None = None) -> None:
        """Load all .template.yaml files from the given directories."""
        if directories is None:
            directories = [Path(__file__).parent / "workloads"]

        self._templates = {}
        for directory in directories:
            if not directory.is_dir():
                continue
            for template_file in sorted(directory.rglob("*.template.yaml")):
                template = self._parse_template(template_file)
                if template:
                    self._templates[template.name] = template
        self._loaded = True

    def get(self, name: str) -> ProjectTemplate | None:
        """Get a template by name."""
        if not self._loaded:
            self.load()
        return self._templates.get(name)

    def list_templates(self) -> list[ProjectTemplate]:
        """Return all loaded templates."""
        if not self._loaded:
            self.load()
        return list(self._templates.values())

    def list_names(self) -> list[str]:
        """Return all template names."""
        if not self._loaded:
            self.load()
        return sorted(self._templates.keys())

    def format_for_prompt(self, category: str | None = None) -> str:
        """Format available templates as text for injection into agent prompts.

        When *category* is given, only templates matching that category are
        included.  Otherwise all templates are listed.

        The output is a concise summary — NOT the full YAML — so it keeps
        the token budget low while giving the agent enough context to
        recommend or adopt the right template.
        """
        templates = self.list_templates()
        if category:
            templates = [t for t in templates if t.category == category]
        if not templates:
            return ""

        lines: list[str] = [
            "## Available Workload Templates\n",
            "When the user's requirements closely match a template below, "
            "adopt that template's service topology and configuration "
            "instead of designing from scratch.  Follow the template's "
            "service names, types, tiers, and config values.\n",
        ]

        for tmpl in templates:
            lines.append(f"### {tmpl.display_name} (`{tmpl.name}`)")
            lines.append(f"{tmpl.description.strip()}")
            lines.append(f"**Category:** {tmpl.category}  ")
            lines.append(f"**Tags:** {', '.join(tmpl.tags)}")
            svc_list = ", ".join(
                f"{s.name} ({s.type})" for s in tmpl.services
            )
            lines.append(f"**Services:** {svc_list}")
            if tmpl.requirements:
                lines.append(f"**Requirements match:** {tmpl.requirements.strip()}")
            lines.append("")

        return "\n".join(lines)

    def _parse_template(self, path: Path) -> ProjectTemplate | None:
        """Parse a .template.yaml file."""
        try:
            data: dict[str, Any] = (
                yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            )
        except Exception:
            logger.warning("Failed to parse template: %s", path)
            return None

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            return None

        services = []
        for s in data.get("services", []):
            if not isinstance(s, dict):
                continue
            services.append(
                TemplateService(
                    name=str(s.get("name", "")),
                    type=str(s.get("type", "")),
                    tier=str(s.get("tier", "")),
                    config=s.get("config", {}),
                )
            )

        return ProjectTemplate(
            name=str(metadata.get("name", path.stem)),
            display_name=str(metadata.get("display_name", metadata.get("name", ""))),
            description=str(metadata.get("description", "")),
            category=str(metadata.get("category", "")),
            services=services,
            iac_defaults=data.get("iac_defaults", {}),
            requirements=str(data.get("requirements", "")),
            tags=metadata.get("tags", []),
        )
