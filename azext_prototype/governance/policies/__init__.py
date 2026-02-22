"""Policy engine — loads and resolves governance policies for agents.

Policies are YAML documents (``*.policy.yaml``) that describe rules,
patterns, anti-patterns, and references that agents must follow when
generating infrastructure and application code.

Built-in policies ship with the extension under this package directory.
Users can extend or override policies by placing additional
``*.policy.yaml`` files in ``.prototype/policies/`` in their project.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Schema constants — keep in sync with the .policy.yaml spec
# ------------------------------------------------------------------ #
SUPPORTED_API_VERSIONS = ("v1",)
SUPPORTED_KINDS = ("policy",)
VALID_SEVERITIES = ("required", "recommended", "optional")
VALID_CATEGORIES = ("azure", "security", "integration", "cost", "data", "general")

# Required top-level keys that every policy file must contain
_REQUIRED_TOP_KEYS = {"metadata"}
_REQUIRED_METADATA_KEYS = {"name", "category", "services"}
_REQUIRED_RULE_KEYS = {"id", "severity", "description", "applies_to"}

# ------------------------------------------------------------------ #
# Data classes
# ------------------------------------------------------------------ #


@dataclass
class PolicyRule:
    """A single governance rule."""

    id: str
    severity: str  # required | recommended | optional
    description: str
    rationale: str = ""
    applies_to: list[str] = field(default_factory=list)


@dataclass
class PolicyPattern:
    """A concrete implementation pattern."""

    name: str
    description: str
    example: str = ""


@dataclass
class Policy:
    """A loaded policy document."""

    name: str
    category: str
    services: list[str] = field(default_factory=list)
    rules: list[PolicyRule] = field(default_factory=list)
    patterns: list[PolicyPattern] = field(default_factory=list)
    anti_patterns: list[dict[str, str]] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    last_reviewed: str = ""


# ------------------------------------------------------------------ #
# Validation
# ------------------------------------------------------------------ #


@dataclass
class ValidationError:
    """A single validation issue found in a policy file."""

    file: str
    message: str
    severity: str = "error"  # error | warning

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.message}"


def validate_policy_file(path: Path) -> list[ValidationError]:
    """Validate a single .policy.yaml file against the schema.

    Returns a list of validation errors (empty means valid).
    """
    errors: list[ValidationError] = []
    filename = str(path)

    # ---- Parse YAML ----
    try:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(ValidationError(filename, f"Invalid YAML: {exc}"))
        return errors
    except OSError as exc:
        errors.append(ValidationError(filename, f"Cannot read file: {exc}"))
        return errors

    if not isinstance(data, dict):
        errors.append(ValidationError(filename, "Root element must be a mapping"))
        return errors

    # ---- apiVersion ----
    api_version = data.get("apiVersion")
    if api_version and api_version not in SUPPORTED_API_VERSIONS:
        errors.append(
            ValidationError(
                filename,
                f"Unsupported apiVersion '{api_version}'. " f"Supported: {', '.join(SUPPORTED_API_VERSIONS)}",
            )
        )

    # ---- kind ----
    kind = data.get("kind")
    if kind and kind not in SUPPORTED_KINDS:
        errors.append(
            ValidationError(
                filename,
                f"Unsupported kind '{kind}'. Supported: {', '.join(SUPPORTED_KINDS)}",
            )
        )

    # ---- metadata ----
    metadata = data.get("metadata")
    if metadata is None:
        errors.append(ValidationError(filename, "Missing required key: 'metadata'"))
        return errors  # can't validate further without metadata

    if not isinstance(metadata, dict):
        errors.append(ValidationError(filename, "'metadata' must be a mapping"))
        return errors

    for key in _REQUIRED_METADATA_KEYS:
        if key not in metadata:
            errors.append(ValidationError(filename, f"metadata missing required key: '{key}'"))

    category = metadata.get("category", "")
    if category and category not in VALID_CATEGORIES:
        errors.append(
            ValidationError(
                filename,
                f"metadata.category '{category}' is not valid. " f"Allowed: {', '.join(VALID_CATEGORIES)}",
                severity="warning",
            )
        )

    services = metadata.get("services")
    if services is not None and not isinstance(services, list):
        errors.append(ValidationError(filename, "metadata.services must be a list"))

    # ---- rules ----
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        errors.append(ValidationError(filename, "'rules' must be a list"))
        rules = []

    rule_ids: set[str] = set()
    for i, rule in enumerate(rules):
        prefix = f"rules[{i}]"
        if not isinstance(rule, dict):
            errors.append(ValidationError(filename, f"{prefix}: must be a mapping"))
            continue

        for key in _REQUIRED_RULE_KEYS:
            if key not in rule:
                errors.append(ValidationError(filename, f"{prefix} missing required key: '{key}'"))

        rid = rule.get("id", "")
        if rid:
            if rid in rule_ids:
                errors.append(ValidationError(filename, f"{prefix}: duplicate rule id '{rid}'"))
            rule_ids.add(rid)

        severity = rule.get("severity", "")
        if severity and severity not in VALID_SEVERITIES:
            errors.append(
                ValidationError(
                    filename,
                    f"{prefix}: invalid severity '{severity}'. " f"Allowed: {', '.join(VALID_SEVERITIES)}",
                )
            )

        applies_to = rule.get("applies_to")
        if applies_to is not None and not isinstance(applies_to, list):
            errors.append(ValidationError(filename, f"{prefix}.applies_to must be a list"))
        elif isinstance(applies_to, list) and len(applies_to) == 0:
            errors.append(
                ValidationError(
                    filename,
                    f"{prefix}.applies_to is empty — rule will never be resolved",
                    severity="warning",
                )
            )

    # ---- patterns (optional) ----
    patterns = data.get("patterns", [])
    if patterns and not isinstance(patterns, list):
        errors.append(ValidationError(filename, "'patterns' must be a list"))
    elif isinstance(patterns, list):
        for i, pat in enumerate(patterns):
            if not isinstance(pat, dict):
                errors.append(ValidationError(filename, f"patterns[{i}]: must be a mapping"))
                continue
            if "name" not in pat:
                errors.append(ValidationError(filename, f"patterns[{i}] missing 'name'"))
            if "description" not in pat:
                errors.append(ValidationError(filename, f"patterns[{i}] missing 'description'"))

    # ---- anti_patterns (optional) ----
    anti_patterns = data.get("anti_patterns", [])
    if anti_patterns and not isinstance(anti_patterns, list):
        errors.append(ValidationError(filename, "'anti_patterns' must be a list"))
    elif isinstance(anti_patterns, list):
        for i, ap in enumerate(anti_patterns):
            if not isinstance(ap, dict):
                errors.append(ValidationError(filename, f"anti_patterns[{i}]: must be a mapping"))
                continue
            if "description" not in ap:
                errors.append(ValidationError(filename, f"anti_patterns[{i}] missing 'description'"))

    # ---- references (optional) ----
    references = data.get("references", [])
    if references and not isinstance(references, list):
        errors.append(ValidationError(filename, "'references' must be a list"))
    elif isinstance(references, list):
        for i, ref in enumerate(references):
            if not isinstance(ref, dict):
                errors.append(ValidationError(filename, f"references[{i}]: must be a mapping"))
                continue
            if "title" not in ref:
                errors.append(ValidationError(filename, f"references[{i}] missing 'title'"))
            if "url" not in ref:
                errors.append(ValidationError(filename, f"references[{i}] missing 'url'"))

    return errors


def validate_policy_directory(directory: Path) -> list[ValidationError]:
    """Validate all .policy.yaml files under a directory recursively.

    Returns a combined list of validation errors across all files.
    """
    all_errors: list[ValidationError] = []
    if not directory.is_dir():
        return all_errors

    for policy_file in sorted(directory.rglob("*.policy.yaml")):
        all_errors.extend(validate_policy_file(policy_file))

    return all_errors


# ------------------------------------------------------------------ #
# Engine
# ------------------------------------------------------------------ #


class PolicyEngine:
    """Loads policies from disk and resolves them for a given agent + context."""

    def __init__(self) -> None:
        self._policies: list[Policy] = []
        self._loaded = False

    def load(self, directories: list[Path] | None = None) -> None:
        """Load all .policy.yaml files from the given directories.

        Default directories:
          1. Built-in policies shipped with the extension
          2. .prototype/policies/ in the user's project (overrides/additions)
        """
        if directories is None:
            directories = [Path(__file__).parent]

        self._policies = []
        for directory in directories:
            if not directory.is_dir():
                continue
            for policy_file in sorted(directory.rglob("*.policy.yaml")):
                policy = self._parse_policy(policy_file)
                if policy:
                    self._policies.append(policy)
        self._loaded = True

    def resolve(
        self,
        agent_name: str,
        services: list[str] | None = None,
        severity: str | None = None,
    ) -> list[Policy]:
        """Return policies relevant to a specific agent and service context.

        Args:
            agent_name: The agent requesting policies (e.g. 'cloud-architect')
            services: Filter to policies mentioning these services
            severity: Minimum severity filter ('required', 'recommended', 'optional')
        """
        if not self._loaded:
            self.load()

        matched: list[Policy] = []
        severity_order = {"required": 0, "recommended": 1, "optional": 2}
        min_severity = severity_order.get(severity or "optional", 2)

        for policy in self._policies:
            # Filter by service if specified
            if services:
                overlap = set(policy.services) & {s.lower() for s in services}
                if not overlap:
                    continue

            # Filter rules that apply to this agent at the requested severity
            relevant_rules = [
                r
                for r in policy.rules
                if (not r.applies_to or agent_name in r.applies_to)
                and severity_order.get(r.severity, 2) <= min_severity
            ]

            if relevant_rules:
                # Return a copy with only the relevant rules
                filtered = Policy(
                    name=policy.name,
                    category=policy.category,
                    services=policy.services,
                    rules=relevant_rules,
                    patterns=policy.patterns,
                    anti_patterns=policy.anti_patterns,
                    references=policy.references,
                    last_reviewed=policy.last_reviewed,
                )
                matched.append(filtered)

        return matched

    def format_for_prompt(
        self,
        agent_name: str,
        services: list[str] | None = None,
    ) -> str:
        """Format resolved policies as text to inject into an agent's system prompt.

        This is the primary integration point — agents call this to get
        governance instructions formatted for the AI.
        """
        policies = self.resolve(agent_name, services, severity="optional")
        if not policies:
            return ""

        sections: list[str] = []
        sections.append("## Governance Policies\n")
        sections.append(
            "You MUST follow all 'required' rules. "
            "You SHOULD follow 'recommended' rules unless there is a "
            "justified reason not to.\n"
        )

        for policy in policies:
            sections.append(f"### {policy.name}")

            for rule in policy.rules:
                marker = "MUST" if rule.severity == "required" else "SHOULD"
                sections.append(f"- [{rule.id}] {marker}: {rule.description}")
                if rule.rationale:
                    sections.append(f"  Rationale: {rule.rationale}")

            if policy.patterns:
                sections.append("\n**Patterns to follow:**")
                for pattern in policy.patterns:
                    sections.append(f"- {pattern.name}: {pattern.description}")
                    if pattern.example:
                        sections.append(f"  ```\n{pattern.example.strip()}\n  ```")

            if policy.anti_patterns:
                sections.append("\n**Anti-patterns to avoid:**")
                for ap in policy.anti_patterns:
                    sections.append(f"- DO NOT: {ap.get('description', '')}")
                    instead = ap.get("instead", "")
                    if instead:
                        sections.append(f"  INSTEAD: {instead}")

            sections.append("")

        return "\n".join(sections)

    def list_policies(self) -> list[Policy]:
        """Return all loaded policies."""
        if not self._loaded:
            self.load()
        return list(self._policies)

    def _parse_policy(self, path: Path) -> Policy | None:
        """Parse a single .policy.yaml file into a Policy object."""
        try:
            data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("Failed to parse policy file: %s", path)
            return None

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            return None

        rules = []
        for r in data.get("rules", []):
            if not isinstance(r, dict):
                continue
            rules.append(
                PolicyRule(
                    id=str(r.get("id", "")),
                    severity=str(r.get("severity", "optional")),
                    description=str(r.get("description", "")),
                    rationale=str(r.get("rationale", "")),
                    applies_to=r.get("applies_to", []),
                )
            )

        patterns = []
        for p in data.get("patterns", []):
            if not isinstance(p, dict):
                continue
            patterns.append(
                PolicyPattern(
                    name=str(p.get("name", "")),
                    description=str(p.get("description", "")),
                    example=str(p.get("example", "")),
                )
            )

        return Policy(
            name=str(metadata.get("name", path.stem)),
            category=str(metadata.get("category", "general")),
            services=metadata.get("services", []),
            rules=rules,
            patterns=patterns,
            anti_patterns=data.get("anti_patterns", []),
            references=data.get("references", []),
            last_reviewed=str(metadata.get("last_reviewed", "")),
        )
