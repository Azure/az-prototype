#!/usr/bin/env python3
"""Generate wiki governance subpages from policy/anti-pattern/standards YAML files."""
import os
import sys
from pathlib import Path

import yaml

GOVERNANCE_DIR = Path(__file__).parent.parent / "azext_prototype" / "governance"
WIKI_DIR = Path(__file__).parent.parent.parent / "azext-prototype-wiki"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def generate_policy_page(title: str, category_path: Path, output_name: str) -> str:
    """Generate a wiki page for a policy category."""
    lines = [f"# {title}", ""]

    yaml_files = sorted(category_path.glob("*.policy.yaml"))
    if not yaml_files:
        lines.append("No policy files found in this category.")
        return "\n".join(lines)

    total_rules = 0
    for yf in yaml_files:
        data = load_yaml(yf)
        rules = data.get("rules", [])
        total_rules += len(rules)

    lines.append(f"**{len(yaml_files)} services, {total_rules} rules**\n")
    lines.append("---\n")

    for yf in yaml_files:
        data = load_yaml(yf)
        meta = data.get("metadata", {})
        service_name = meta.get("name", yf.stem.replace(".policy", ""))
        rules = data.get("rules", [])
        anti_patterns = data.get("anti_patterns", [])
        references = data.get("references", [])

        lines.append(f"## {service_name.replace('-', ' ').title()}")
        lines.append("")
        lines.append(f"**File**: `{yf.name}`")
        services = meta.get("services", [])
        if services:
            lines.append(f"**Services**: {', '.join(services)}")
        lines.append("")

        if rules:
            lines.append("| Policy ID | Description | Agents |")
            lines.append("| --------- | ----------- | ------ |")
            for rule in rules:
                rid = rule.get("id", "?")
                severity = rule.get("severity", "?")
                desc = rule.get("description", "").replace("|", "\\|").replace("\n", " ")
                applies_to = rule.get("applies_to", [])
                prohibitions = rule.get("prohibitions", [])

                # Build description cell with severity, prohibitions
                cell = f"**[{severity}]** {desc}"
                if prohibitions:
                    prohib_text = "<br />".join(f"NEVER: {p}" for p in prohibitions)
                    cell += f"<br /><br />{prohib_text}"

                # Agents column
                if applies_to:
                    agents_text = ", ".join(f"`{a}`" for a in applies_to)
                else:
                    agents_text = "_all agents_"

                lines.append(f"| {rid} | {cell} | {agents_text} |")
            lines.append("")

        if anti_patterns:
            lines.append("### Anti-Patterns\n")
            for ap in anti_patterns:
                desc = ap.get("description", "")
                instead = ap.get("instead", "")
                lines.append(f"- **Don't**: {desc}")
                if instead:
                    lines.append(f"  **Instead**: {instead}")
            lines.append("")

        if references:
            lines.append("<details><summary>References</summary>\n")
            for ref in references:
                title_text = ref.get("title", "Link")
                url = ref.get("url", "")
                lines.append(f"- [{title_text}]({url})")
            lines.append("\n</details>\n")

        lines.append("---\n")

    return "\n".join(lines)


def generate_anti_patterns_page() -> str:
    """Generate the anti-patterns wiki page."""
    lines = ["# Anti-Patterns", ""]
    lines.append(
        "Anti-patterns are automatically detected in AI-generated output after each stage. "
        "When a pattern matches and no safe pattern exempts it, a warning is shown.\n"
    )

    ap_dir = GOVERNANCE_DIR / "anti_patterns"
    yaml_files = sorted(ap_dir.glob("*.yaml"))

    total = 0
    for yf in yaml_files:
        data = load_yaml(yf)
        patterns = data.get("patterns", [])
        total += len(patterns)

    lines.append(f"**{len(yaml_files)} domains, {total} checks**\n")
    lines.append("---\n")

    for yf in yaml_files:
        data = load_yaml(yf)
        domain = data.get("domain", yf.stem)
        description = data.get("description", "")
        patterns = data.get("patterns", [])

        lines.append(f"## {domain.replace('_', ' ').title()}")
        lines.append(f"\n{description}\n")
        if patterns:
            lines.append("| Check | Description | Agents |")
            lines.append("| ----- | ----------- | ------ |")
            for i, p in enumerate(patterns, 1):
                warning = p.get("warning_message", "").replace("|", "\\|").replace("\n", " ")
                search = p.get("search_patterns", [])
                safe = p.get("safe_patterns", [])

                # Build description cell
                cell = warning
                if search:
                    triggers = ", ".join(f"`{s}`" for s in search[:5])
                    cell += f"<br /><br />Triggers on: {triggers}"
                if safe:
                    exemptions = ", ".join(f"`{s}`" for s in safe[:5])
                    cell += f"<br />Exempted by: {exemptions}"

                lines.append(f"| {domain.upper()}-{i:03d} | {cell} | _all agents_ |")
            lines.append("")

        lines.append("---\n")

    return "\n".join(lines)


def generate_standards_page() -> str:
    """Generate the standards wiki page."""
    lines = ["# Design Standards", ""]
    lines.append(
        "Design standards are injected into agent system messages to guide code quality. "
        "They cover design principles, coding conventions, and IaC module patterns.\n"
    )

    std_dir = GOVERNANCE_DIR / "standards"
    yaml_files = sorted(std_dir.rglob("*.yaml"))

    total = 0
    for yf in yaml_files:
        data = load_yaml(yf)
        principles = data.get("principles", data.get("standards", []))
        total += len(principles)

    lines.append(f"**{len(yaml_files)} documents, {total} principles**\n")
    lines.append("---\n")

    for yf in yaml_files:
        data = load_yaml(yf)
        meta = data.get("metadata", {})
        name = meta.get("name", yf.stem)
        description = meta.get("description", "")
        principles = data.get("principles", data.get("standards", []))

        lines.append(f"## {name.replace('-', ' ').replace('_', ' ').title()}")
        if description:
            lines.append(f"\n{description}\n")

        if principles:
            lines.append("| ID | Principle | Rationale |")
            lines.append("|-----|-----------|-----------|")
            for p in principles:
                pid = p.get("id", "?")
                principle = p.get("name", p.get("principle", "?")).replace("|", "\\|")
                rationale = p.get("rationale", p.get("description", "")).replace("|", "\\|")[:100]
                lines.append(f"| {pid} | {principle} | {rationale} |")
            lines.append("")

        lines.append("---\n")

    return "\n".join(lines)


def main():
    os.makedirs(WIKI_DIR, exist_ok=True)

    # Azure policy subpages
    azure_dir = GOVERNANCE_DIR / "policies" / "azure"
    azure_categories = {
        "ai": "Azure AI Services Policies",
        "compute": "Azure Compute Policies",
        "data": "Azure Data Services Policies",
        "identity": "Azure Identity Policies",
        "management": "Azure Management Policies",
        "messaging": "Azure Messaging Policies",
        "monitoring": "Azure Monitoring Policies",
        "networking": "Azure Networking Policies",
        "security": "Azure Security Policies",
        "storage": "Azure Storage Policies",
        "web": "Azure Web & App Policies",
    }

    for subdir, title in azure_categories.items():
        cat_path = azure_dir / subdir
        if cat_path.exists():
            content = generate_policy_page(title, cat_path, f"Governance-Policies-Azure-{subdir.title()}")
            out_path = WIKI_DIR / f"Governance-Policies-Azure-{subdir.title()}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  {out_path.name}")

    # Non-Azure policy subpages
    other_categories = {
        "cost": "Cost Optimization Policies",
        "integration": "Integration Pattern Policies",
        "performance": "Performance Policies",
        "reliability": "Reliability Policies",
    }

    for subdir, title in other_categories.items():
        cat_path = GOVERNANCE_DIR / "policies" / subdir
        if cat_path.exists():
            content = generate_policy_page(title, cat_path, f"Governance-Policies-{subdir.title()}")
            out_path = WIKI_DIR / f"Governance-Policies-{subdir.title()}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  {out_path.name}")

    # Anti-patterns page
    content = generate_anti_patterns_page()
    out_path = WIKI_DIR / "Governance-Anti-Patterns.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"  {out_path.name}")

    # Standards page
    content = generate_standards_page()
    out_path = WIKI_DIR / "Governance-Standards.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"  {out_path.name}")

    print(f"\nGenerated {len(azure_categories) + len(other_categories) + 2} wiki pages.")


if __name__ == "__main__":
    main()
