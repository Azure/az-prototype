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
# Schema constants — keep in sync with governance/schemas/policy.schema.json
# ------------------------------------------------------------------ #
SUPPORTED_KINDS = ("policy",)
VALID_SEVERITIES = ("required", "recommended", "optional")

# Required top-level keys (new unified format)
_REQUIRED_TOP_KEYS = {"kind", "category", "description", "last_updated", "rules"}
_REQUIRED_RULE_KEYS = {"id", "severity", "description"}

# ------------------------------------------------------------------ #
# Data classes
# ------------------------------------------------------------------ #


@dataclass
class CompanionResource:
    """A resource that must accompany the primary resource."""

    type: str
    description: str
    name: str = ""
    terraform_pattern: str = ""
    bicep_pattern: str = ""


@dataclass
class PolicyRule:
    """A single governance rule."""

    id: str
    severity: str  # required | recommended | optional
    description: str
    rationale: str = ""
    warning_message: str = ""
    applies_to: list[str] = field(default_factory=list)
    targets: list = field(
        default_factory=list
    )  # [{"services": [...], "terraform_pattern": "...", "prohibitions": [...]}]
    companion_resources: list[CompanionResource] = field(default_factory=list)


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
    description: str = ""
    last_updated: str = ""
    services: list[str] = field(default_factory=list)  # backward compat aggregate
    rules: list[PolicyRule] = field(default_factory=list)
    patterns: list[PolicyPattern] = field(default_factory=list)
    anti_patterns: list[dict[str, str]] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)


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
    """Validate a single .policy.yaml file against the unified schema.

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

    # ---- kind ----
    kind = data.get("kind")
    if kind and kind not in SUPPORTED_KINDS:
        errors.append(
            ValidationError(
                filename,
                f"Unsupported kind '{kind}'. Supported: {', '.join(SUPPORTED_KINDS)}",
            )
        )

    # ---- Validate required top-level keys ----
    for key in _REQUIRED_TOP_KEYS:
        if key not in data:
            errors.append(ValidationError(filename, f"Missing required key: '{key}'"))

    # ---- rules ----
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        errors.append(ValidationError(filename, "'rules' must be a list"))
        rules = []

    # Same ID is allowed with different targets (different services)
    rule_id_targets: set[tuple] = set()
    for i, rule in enumerate(rules):
        prefix = f"rules[{i}]"
        if not isinstance(rule, dict):
            errors.append(ValidationError(filename, f"{prefix}: must be a mapping"))
            continue

        for key in _REQUIRED_RULE_KEYS:
            if key not in rule:
                errors.append(ValidationError(filename, f"{prefix} missing required key: '{key}'"))

        rid = rule.get("id", "")
        targets = rule.get("targets", [])
        if isinstance(targets, dict):
            targets = [targets]
        all_svcs = []
        for t in targets if isinstance(targets, list) else []:
            all_svcs.extend(t.get("services", []) if isinstance(t, dict) else [])
        target_svcs = tuple(sorted(all_svcs))
        key = (rid, target_svcs)
        if rid:
            if key in rule_id_targets:
                errors.append(
                    ValidationError(filename, f"{prefix}: duplicate rule id+targets '{rid}' for {target_svcs}")
                )
            rule_id_targets.add(key)

        severity = rule.get("severity", "")
        if severity and severity not in VALID_SEVERITIES:
            errors.append(
                ValidationError(
                    filename,
                    f"{prefix}: invalid severity '{severity}'. Allowed: {', '.join(VALID_SEVERITIES)}",
                )
            )

        applies_to = rule.get("applies_to")
        if applies_to is not None and not isinstance(applies_to, list):
            errors.append(ValidationError(filename, f"{prefix}.applies_to must be a list"))

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
                policy_svcs = {s.lower() for s in policy.services}
                overlap = policy_svcs & {s.lower() for s in services}
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

    def resolve_for_stage(
        self,
        services: list[str],
        iac_tool: str,
        agent_name: str = "",
    ) -> str:
        """Resolve and format deterministic policies for a stage's services.

        Uses **exact service matching** (not embeddings) to find all
        policies that apply to the named services.  Returns a formatted
        brief with the IaC-specific code patterns (terraform or bicep),
        companion resources, and prohibitions.
        """
        if not self._loaded:
            self.load()

        if not services:
            return ""

        svc_set = {s.lower() for s in services}
        matched_policies = []
        for p in self._policies:
            # Match by aggregate policy.services (legacy + new targets union)
            policy_svcs = {s.lower() for s in p.services}
            overlap = policy_svcs & svc_set
            if not overlap:
                # Also try per-rule targets[].services
                rule_targets = {
                    s.lower() for r in p.rules for t in r.targets if isinstance(t, dict) for s in t.get("services", [])
                }
                overlap = rule_targets & svc_set
            if not overlap:
                continue
            # Only include if the majority of the policy's services are in the stage,
            # OR the policy is service-specific (1-2 services).
            if len(policy_svcs) <= 2 or len(overlap) >= max(len(policy_svcs), 1) / 2:
                matched_policies.append(p)
        if not matched_policies:
            return ""

        pattern_key = "terraform_pattern" if iac_tool == "terraform" else "bicep_pattern"
        sections: list[str] = []

        for policy in matched_policies:
            rules = [
                r
                for r in policy.rules
                if r.severity == "required" and (not agent_name or not r.applies_to or agent_name in r.applies_to)
            ]
            if not rules:
                continue

            sections.append(f"### {policy.name}")

            for rule in rules:
                sections.append(f"\n**[{rule.id}] {rule.description}**")
                if rule.rationale:
                    sections.append(f"Rationale: {rule.rationale}")

                # Find matching target entry for the requested services
                for target in rule.targets:
                    if not isinstance(target, dict):
                        continue
                    target_svcs = {s.lower() for s in target.get("services", [])}
                    if target_svcs and not (target_svcs & svc_set):
                        continue  # This target entry is for a different service
                    pattern = target.get(pattern_key, "") or ""
                    if isinstance(pattern, str) and pattern.strip():
                        sections.append(f"```\n{pattern.strip()}\n```")
                    prohibitions = target.get("prohibitions", [])
                    if prohibitions:
                        for p in prohibitions:
                            sections.append(f"- NEVER: {p}")

                for cr in rule.companion_resources:
                    sections.append(f"\nCOMPANION RESOURCE: {cr.description}")
                    cr_pattern = getattr(cr, pattern_key, "") or ""
                    if cr_pattern.strip():
                        sections.append(f"```\n{cr_pattern.strip()}\n```")

                prohibitions = []  # already handled per-target above
                if prohibitions:
                    for p in prohibitions:
                        sections.append(f"- NEVER: {p}")

            sections.append("")

        if not sections:
            return ""

        header = (
            "## MANDATORY RESOURCE POLICIES\n\n"
            "The following policies define the REQUIRED baseline configuration for each resource.\n"
            "You MUST include all properties, companion resources, and patterns specified below.\n"
            "You MAY add additional properties required by the architecture (SKUs, database names,\n"
            "app settings, etc.), but you must NEVER omit or contradict a policy directive.\n\n"
            'If a policy says "NEVER use X", do not use X under any circumstances.\n'
            "If a policy provides exact code, use it as your starting template and extend as needed.\n"
        )

        return header + "\n".join(sections)

    def list_policies(self) -> list[Policy]:
        """Return all loaded policies."""
        if not self._loaded:
            self.load()
        return list(self._policies)

    def _parse_policy(self, path: Path) -> Policy | None:
        """Parse a single .policy.yaml file into a Policy object.

        Supports both the new unified format (flat top-level keys) and
        the legacy format (apiVersion + metadata wrapper) for backward
        compatibility during migration.
        """
        try:
            data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("Failed to parse policy file: %s", path)
            return None

        if not isinstance(data, dict):
            return None

        policy_name = path.stem.replace(".policy", "")
        policy_category = str(data.get("category", "general"))
        policy_description = str(data.get("description", ""))
        policy_last_updated = str(data.get("last_updated", ""))

        rules = []
        all_target_services: set[str] = set()
        for r in data.get("rules", []):
            if not isinstance(r, dict):
                continue
            companions = []
            for cr in r.get("companion_resources", []):
                if isinstance(cr, dict):
                    companions.append(
                        CompanionResource(
                            type=str(cr.get("type", "")),
                            description=str(cr.get("description", "")),
                            name=str(cr.get("name", "")),
                            terraform_pattern=str(cr.get("terraform_pattern", "")),
                            bicep_pattern=str(cr.get("bicep_pattern", "")),
                        )
                    )
            # targets is a list of target blocks
            targets_raw = r.get("targets", [])
            if isinstance(targets_raw, dict):
                # Normalize single dict to list
                targets_raw = [targets_raw]
            if not isinstance(targets_raw, list):
                targets_raw = []
            for t in targets_raw:
                if isinstance(t, dict):
                    all_target_services.update(t.get("services", []))

            rules.append(
                PolicyRule(
                    id=str(r.get("id", "")),
                    severity=str(r.get("severity", "optional")),
                    description=str(r.get("description", "")),
                    rationale=str(r.get("rationale", "")),
                    warning_message=str(r.get("warning_message", "")),
                    applies_to=r.get("applies_to", []),
                    targets=targets_raw,
                    companion_resources=companions,
                )
            )

        # Aggregate services from all per-rule targets
        aggregate_services = list(all_target_services)

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
            name=policy_name,
            category=policy_category,
            description=policy_description,
            last_updated=policy_last_updated,
            services=aggregate_services,
            rules=rules,
            patterns=patterns,
            anti_patterns=data.get("anti_patterns", []),
            references=data.get("references", []),
        )
