#!/usr/bin/env python3
"""Generate wiki governance pages from policy/anti-pattern/standards YAML files.

Reads governance YAML files and renders wiki markdown pages using the format
defined in ``scripts/templates/``.  Output goes to the wiki directory at
``../azext-prototype-wiki/``.

Each YAML file gets its own wiki page.  The sidebar is regenerated with
grouped sections (e.g., "Compute" header with AKS, Batch subpages).

Usage::

    python scripts/generate_wiki_governance.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

GOVERNANCE_DIR = Path(__file__).parent.parent / "azext_prototype" / "governance"
WIKI_DIR = Path(__file__).parent.parent.parent / "azext-prototype-wiki"


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------
# Abbreviation / capitalization map
# ---------------------------------------------------------------

_ABBREVIATIONS: dict[str, str] = {
    "aks": "AKS",
    "apim": "APIM",
    "api": "API",
    "sql": "SQL",
    "vmss": "VMSS",
    "vm": "VM",
    "vms": "VMs",
    "dns": "DNS",
    "cdn": "CDN",
    "iot": "IoT",
    "ai": "AI",
    "ml": "ML",
    "rbac": "RBAC",
    "tls": "TLS",
    "ssl": "SSL",
    "nsg": "NSG",
    "ddos": "DDoS",
    "waf": "WAF",
    "bll": "BLL",
    "di": "DI",
    "orm": "ORM",
    "dto": "DTO",
    "sdk": "SDK",
}


def _title_case(name: str) -> str:
    """Title-case a name, respecting known abbreviations."""
    words = name.replace("-", " ").replace("_", " ").split()
    result = []
    for w in words:
        lower = w.lower()
        if lower in _ABBREVIATIONS:
            result.append(_ABBREVIATIONS[lower])
        else:
            result.append(w.capitalize())
    return " ".join(result)


def _strip_api_version(resource_type: str) -> str:
    """Strip @YYYY-MM-DD or @YYYY-MM-DD-preview from a resource type."""
    return re.sub(r"@[\d-]+(-preview)?$", "", resource_type)


# ---------------------------------------------------------------
# Anti-pattern pages (one per YAML file)
# ---------------------------------------------------------------


def _render_anti_pattern_page(yf: Path) -> str:
    """Render one anti-pattern domain file to wiki markdown."""
    data = _load_yaml(yf)
    domain = data.get("domain", yf.stem)
    title = _title_case(domain)
    description = data.get("description", "")
    patterns = data.get("patterns", [])

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append(description)
    lines.append("")
    lines.append(f"**Domain:** `{domain}`")
    lines.append("")
    lines.append("<hr />")
    lines.append("")

    # Summary table
    lines.append(f"### Checks ({len(patterns)})")
    lines.append("")
    lines.append("| Check | Description |")
    lines.append("| ----- | ----------- |")
    for p in patterns:
        check_id = p.get("id", "?")
        warning = p.get("warning_message", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| [{check_id}](#{check_id}) | {warning} |")
    lines.append("")
    lines.append("<hr />")
    lines.append("")

    # Per-check detail sections
    for p in patterns:
        check_id = p.get("id", "?")
        warning = p.get("warning_message", "")
        rationale = p.get("rationale", "")
        applies_to = p.get("applies_to", [])
        targets = p.get("targets", {})

        # targets can be a list of dicts or a dict
        if isinstance(targets, list):
            all_services: list[str] = []
            all_search: list[str] = []
            all_correct: list[str] = []
            for t in targets:
                if isinstance(t, dict):
                    all_services.extend(t.get("services", []))
                    all_search.extend(t.get("search_patterns", []))
                    all_correct.extend(t.get("correct_patterns", []))
        elif isinstance(targets, dict):
            all_services = targets.get("services", [])
            all_search = targets.get("search_patterns", [])
            all_correct = targets.get("correct_patterns", [])
        else:
            all_services, all_search, all_correct = [], [], []

        lines.append(f"## {check_id}")
        lines.append(warning)
        lines.append("")
        if rationale:
            lines.append(f"**Rationale:** {rationale}  ")
        agents = ", ".join(applies_to) if applies_to else "_all agents_"
        lines.append(f"**Agents:** `{agents}`")
        lines.append("")

        # Targets table
        lines.append("### Targets")
        lines.append("")

        svc_html = (
            "<ul>" + "".join(f"<li>{s}</li>" for s in all_services) + "</ul>"
            if all_services
            else "*All*"
        )
        search_html = (
            "<ul>" + "".join(f"<li>'{s}'</li>" for s in all_search) + "</ul>"
            if all_search
            else ""
        )
        correct_html = (
            "<ul>" + "".join(f"<li>'{s}'</li>" for s in all_correct) + "</ul>"
            if all_correct
            else ""
        )

        lines.append("| Services  | Triggers On | Correct Patterns |")
        lines.append("| --------- | ----------- | ---------------- |")
        lines.append(f"| {svc_html}|{search_html}|{correct_html}|")
        lines.append("")
        lines.append("<hr />")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------
# Standards pages (one per YAML file)
# ---------------------------------------------------------------


def _render_standard_page(yf: Path) -> str:
    """Render one standards file to wiki markdown."""
    data = _load_yaml(yf)
    title = _title_case(yf.stem)
    domain = data.get("domain", "")
    description = data.get("description", "")
    principles = data.get("principles", [])

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append(description)
    lines.append("")
    lines.append(f"**Domain:** `{domain}`")
    lines.append("")
    lines.append("<hr />")
    lines.append("")

    # Summary table
    lines.append(f"### Checks ({len(principles)})")
    lines.append("")
    lines.append("| Check | Description |")
    lines.append("| ----- | ----------- |")
    for p in principles:
        pid = p.get("id", "?")
        desc = p.get("description", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f'| <span style="text-wrap:nowrap;">[{pid}](#{pid})</span> | {desc} |')
    lines.append("")
    lines.append("<hr />")
    lines.append("")

    # Per-principle detail sections
    for p in principles:
        pid = p.get("id", "?")
        desc = p.get("description", "")
        rationale = p.get("rationale", "")
        applies_to = p.get("applies_to", [])
        examples = p.get("examples", [])

        lines.append(f"## {pid}")
        lines.append(desc)
        lines.append("")
        if rationale:
            lines.append(f"**Rationale:** {rationale}  ")
        agents = ", ".join(applies_to) if applies_to else "_all agents_"
        lines.append(f"**Agents:** `{agents}`")
        lines.append("")

        if examples:
            lines.append("### Examples")
            lines.append("")
            for ex in examples:
                lines.append(f"- {ex}")
            lines.append("")

        lines.append("<hr />")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------
# Policy pages (one per YAML file)
# ---------------------------------------------------------------


def _render_policy_page(yf: Path) -> str:
    """Render one policy YAML file to a wiki page."""
    data = _load_yaml(yf)
    raw_name = yf.stem.replace(".policy", "")
    service_name = _title_case(raw_name)
    description = data.get("description", f"Governance policies for {service_name}")
    file_domain = data.get("domain", "")
    rules = data.get("rules", [])
    anti_patterns_list = data.get("anti_patterns", [])
    references = data.get("references", [])
    patterns_list = data.get("patterns", [])

    lines: list[str] = []
    lines.append(f"# {service_name}")
    lines.append(description)
    lines.append("")
    lines.append(f"**Domain:** `{file_domain}`")
    lines.append("")

    # Patterns section
    if patterns_list:
        lines.append("### Patterns")
        lines.append("")
        lines.append("| Name | Description |")
        lines.append("| ---- | ----------- |")
        for pat in patterns_list:
            name = pat.get("name", "")
            desc = pat.get("description", "").replace("|", "\\|")
            lines.append(f"| {name} | {desc} |")
        lines.append("")

    # Anti-patterns section
    if anti_patterns_list:
        lines.append("### Anti-Patterns")
        lines.append("")
        lines.append("| Description | Instead |")
        lines.append("| ----------- | ------- |")
        for ap in anti_patterns_list:
            desc = ap.get("description", "").replace("|", "\\|")
            instead = ap.get("instead", "").replace("|", "\\|")
            lines.append(f"| {desc} | {instead} |")
        lines.append("")

    # References section
    if references:
        lines.append("### References")
        lines.append("")
        for ref in references:
            ref_title = ref.get("title", "Link")
            url = ref.get("url", "")
            lines.append(f"- [{ref_title}]({url})")
        lines.append("")

    lines.append("<hr />")
    lines.append("")

    # Rules summary table
    lines.append(f"### Checks ({len(rules)})")
    lines.append("")
    lines.append("| Check | Severity | Description |")
    lines.append("| ----- | -------- | ----------- |")
    for rule in rules:
        rid = rule.get("id", "?")
        severity = rule.get("severity", "?").title()
        desc = rule.get("description", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f'| <span style="text-wrap:nowrap;">[{rid}](#{rid})</span> | {severity} | {desc} |')
    lines.append("")
    lines.append("<hr />")
    lines.append("")

    # Per-rule detail sections
    for rule in rules:
        rid = rule.get("id", "?")
        desc = rule.get("description", "")
        severity = rule.get("severity", "?").title()
        rationale = rule.get("rationale", "")
        applies_to = rule.get("applies_to", [])
        targets = rule.get("targets", [])
        companion_resources = rule.get("companion_resources", [])
        prohibitions = rule.get("prohibitions", [])

        lines.append(f"## {rid}")
        lines.append(desc)
        lines.append("")
        lines.append(f"**Severity:** {severity}  ")
        if rationale:
            lines.append(f"**Rationale:** {rationale}  ")
        agents = ", ".join(applies_to) if applies_to else "_all agents_"
        lines.append(f"**Agents:** `{agents}`")
        lines.append("")

        # Targets
        if targets:
            lines.append("### Targets")
            lines.append("")
            for t in targets:
                if isinstance(t, dict):
                    for svc in t.get("services", []):
                        lines.append(f"- {svc}")
                else:
                    lines.append(f"- {t}")
            lines.append("")

        # Companion resources
        if companion_resources:
            lines.append("### Companion Resources")
            lines.append("")
            lines.append("| Resource | Name | Purpose |")
            lines.append("| -------- | ---- | ------- |")
            for cr in companion_resources:
                res_type = _strip_api_version(cr.get("type", ""))
                name = cr.get("name", "")
                purpose = cr.get("description", "").replace("|", "\\|")
                lines.append(
                    f'| <span style="text-wrap:nowrap;">{res_type}</span> '
                    f'| <span style="text-wrap:nowrap;">{name}</span> | {purpose} |'
                )
            lines.append("")

        # Prohibitions
        if prohibitions:
            lines.append("### Prohibitions")
            lines.append("")
            for p in prohibitions:
                lines.append(f"- {p}")
            lines.append("")

        lines.append("<hr />")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------
# Sidebar generation
# ---------------------------------------------------------------


def _generate_sidebar(
    policy_pages: dict[str, list[tuple[str, str]]],
    ap_pages: list[tuple[str, str]],
    std_pages: dict[str, list[tuple[str, str]]],
) -> str:
    """Generate the governance section of _Sidebar.md.

    Args:
        policy_pages: {section_title: [(display_name, wiki_filename), ...]}
        ap_pages: [(display_name, wiki_filename), ...]
        std_pages: {section_title: [(display_name, wiki_filename), ...]}
    """
    # Read existing sidebar and replace/append governance section
    sidebar_path = WIKI_DIR / "_Sidebar.md"
    if sidebar_path.exists():
        existing = sidebar_path.read_text(encoding="utf-8")
    else:
        existing = ""

    # Find and replace the governance block (between markers)
    marker_start = "<!-- GOVERNANCE START -->"
    marker_end = "<!-- GOVERNANCE END -->"

    lines: list[str] = []
    lines.append(marker_start)
    lines.append("")
    lines.append("### Governance")
    lines.append("")

    # Policies — one <details> per category
    for section_title, pages in sorted(policy_pages.items()):
        lines.append(f"<details><summary>Policies — {section_title}</summary>")
        lines.append("")
        for display, filename in pages:
            lines.append(f"- [{display}]({filename})")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Anti-patterns — single <details>
    lines.append("<details><summary>Anti-Patterns</summary>")
    lines.append("")
    for display, filename in ap_pages:
        lines.append(f"- [{display}]({filename})")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Standards — one <details> per section
    for section_title, pages in sorted(std_pages.items()):
        lines.append(f"<details><summary>Standards — {section_title}</summary>")
        lines.append("")
        for display, filename in pages:
            lines.append(f"- [{display}]({filename})")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append(marker_end)

    gov_block = "\n".join(lines)

    if marker_start in existing:
        # Replace existing governance block
        pattern = re.compile(
            re.escape(marker_start) + r".*?" + re.escape(marker_end),
            re.DOTALL,
        )
        new_sidebar = pattern.sub(gov_block, existing)
    else:
        # Append at end
        new_sidebar = existing.rstrip() + "\n\n" + gov_block + "\n"

    return new_sidebar


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

# Azure policy subdirectories → section titles
_AZURE_SECTIONS = {
    "ai": "AI Services",
    "compute": "Compute",
    "data": "Data Services",
    "identity": "Identity",
    "management": "Management",
    "messaging": "Messaging",
    "monitoring": "Monitoring",
    "networking": "Networking",
    "security": "Security",
    "storage": "Storage",
    "web": "Web & App",
}

# Non-Azure policy subdirectories
_OTHER_SECTIONS = {
    "cost": "Cost Optimization",
    "integration": "Integration",
    "performance": "Performance",
    "reliability": "Reliability",
    "security": "Security Principles",
}

# Standards subdirectories
_STANDARDS_SECTIONS = {
    "application": "Application",
    "iac": "IaC",
    "principles": "Principles",
}


def main() -> None:
    os.makedirs(WIKI_DIR, exist_ok=True)
    page_count = 0

    # Track pages for sidebar
    policy_sidebar: dict[str, list[tuple[str, str]]] = {}
    ap_sidebar: list[tuple[str, str]] = []
    std_sidebar: dict[str, list[tuple[str, str]]] = {}

    # --- Policy pages (one per YAML file) ---
    azure_dir = GOVERNANCE_DIR / "policies" / "azure"
    for subdir, section_title in _AZURE_SECTIONS.items():
        cat_path = azure_dir / subdir
        if not cat_path.exists():
            continue
        section_pages: list[tuple[str, str]] = []
        for yf in sorted(cat_path.glob("*.policy.yaml")):
            raw_name = yf.stem.replace(".policy", "")
            display_name = _title_case(raw_name)
            wiki_filename = f"Governance-Policies-Azure-{_title_case(subdir).replace(' ', '-')}-{display_name.replace(' ', '-')}"
            content = _render_policy_page(yf)
            out_path = WIKI_DIR / f"{wiki_filename}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  {out_path.name}")
            section_pages.append((display_name, wiki_filename))
            page_count += 1
        if section_pages:
            policy_sidebar[f"Azure {section_title}"] = section_pages

    for subdir, section_title in _OTHER_SECTIONS.items():
        cat_path = GOVERNANCE_DIR / "policies" / subdir
        if not cat_path.exists():
            continue
        section_pages = []
        for yf in sorted(cat_path.glob("*.policy.yaml")):
            raw_name = yf.stem.replace(".policy", "")
            display_name = _title_case(raw_name)
            wiki_filename = f"Governance-Policies-{_title_case(subdir).replace(' ', '-')}-{display_name.replace(' ', '-')}"
            content = _render_policy_page(yf)
            out_path = WIKI_DIR / f"{wiki_filename}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  {out_path.name}")
            section_pages.append((display_name, wiki_filename))
            page_count += 1
        if section_pages:
            policy_sidebar[section_title] = section_pages

    # --- Anti-pattern pages (one per YAML file) ---
    ap_dir = GOVERNANCE_DIR / "anti_patterns"
    for yf in sorted(ap_dir.glob("*.yaml")):
        data = _load_yaml(yf)
        domain = data.get("domain", yf.stem)
        display_name = _title_case(domain)
        wiki_filename = f"Governance-Anti-Patterns-{display_name.replace(' ', '-')}"
        content = _render_anti_pattern_page(yf)
        out_path = WIKI_DIR / f"{wiki_filename}.md"
        out_path.write_text(content, encoding="utf-8")
        print(f"  {out_path.name}")
        ap_sidebar.append((display_name, wiki_filename))
        page_count += 1

    # --- Standards pages (one per YAML file) ---
    std_dir = GOVERNANCE_DIR / "standards"
    for section_subdir, section_title in _STANDARDS_SECTIONS.items():
        section_path = std_dir / section_subdir
        if not section_path.is_dir():
            continue
        section_pages = []
        for yf in sorted(section_path.glob("*.yaml")):
            display_name = _title_case(yf.stem)
            wiki_filename = f"Governance-Standards-{_title_case(section_subdir)}-{display_name.replace(' ', '-')}"
            content = _render_standard_page(yf)
            out_path = WIKI_DIR / f"{wiki_filename}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  {out_path.name}")
            section_pages.append((display_name, wiki_filename))
            page_count += 1
        if section_pages:
            std_sidebar[section_title] = section_pages

    # --- Update sidebar ---
    new_sidebar = _generate_sidebar(policy_sidebar, ap_sidebar, std_sidebar)
    sidebar_path = WIKI_DIR / "_Sidebar.md"
    sidebar_path.write_text(new_sidebar, encoding="utf-8")
    print(f"  {sidebar_path.name} (updated)")

    # --- Clean up old grouped pages ---
    old_patterns = [
        "Governance-Policies-Azure-*.md",
        "Governance-Policies-Cost.md",
        "Governance-Policies-Integration.md",
        "Governance-Policies-Performance.md",
        "Governance-Policies-Reliability.md",
        "Governance-Policies-Security.md",
    ]
    # Only remove old grouped files that don't match new individual files
    for pattern in old_patterns:
        for old_file in WIKI_DIR.glob(pattern):
            # Check if this is a new per-service page (has 4+ hyphen-separated parts)
            parts = old_file.stem.split("-")
            if len(parts) <= 4:  # Old grouped page like "Governance-Policies-Azure-Compute"
                old_file.unlink()
                print(f"  Removed old: {old_file.name}")

    print(f"\nGenerated {page_count} wiki governance pages.")


if __name__ == "__main__":
    main()
