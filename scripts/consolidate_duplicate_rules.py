#!/usr/bin/env python3
"""Consolidate duplicate rule IDs in policy YAML files.

For each *.policy.yaml file, if the same rule ID appears multiple times:
- Keep the first occurrence (with all its fields: description, rationale, severity, etc.)
- Merge targets from all duplicates into the first occurrence's targets array
- Merge companion_resources (deduplicated by type)
- Remove the duplicate entries

Usage:
    python scripts/consolidate_duplicate_rules.py              # dry-run (report only)
    python scripts/consolidate_duplicate_rules.py --apply      # apply changes
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# YAML helpers — preserve key order, use block style for readability
# ---------------------------------------------------------------------------

class _OrderedDumper(yaml.SafeDumper):
    """Dump dicts in insertion order, use block style for multiline strings."""
    pass


def _dict_representer(dumper: yaml.Dumper, data: dict) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_OrderedDumper.add_representer(dict, _dict_representer)
_OrderedDumper.add_representer(str, _str_representer)


def _dump_yaml(data: dict) -> str:
    return yaml.dump(
        data,
        Dumper=_OrderedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


# ---------------------------------------------------------------------------
# Core consolidation logic
# ---------------------------------------------------------------------------

def _dedup_companion_resources(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge companion_resources, deduplicating by 'type' field."""
    seen_types: set[str] = set()
    merged: list[dict] = []
    for cr in existing + new:
        cr_type = cr.get("type", "")
        if cr_type not in seen_types:
            seen_types.add(cr_type)
            merged.append(cr)
    return merged


def consolidate_rules(rules: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Consolidate duplicate rule IDs into single entries.

    Returns:
        (consolidated_rules, {rule_id: original_count} for duplicates)
    """
    # Count occurrences
    id_counts = Counter(r["id"] for r in rules if "id" in r)
    duplicates = {rid: cnt for rid, cnt in id_counts.items() if cnt > 1}

    if not duplicates:
        return rules, {}

    # Build consolidated list, preserving order of first occurrence
    consolidated: list[dict] = []
    seen: dict[str, int] = {}  # rule_id -> index in consolidated

    for rule in rules:
        rid = rule.get("id")
        if rid is None:
            consolidated.append(rule)
            continue

        if rid not in seen:
            # First occurrence — keep it as-is
            seen[rid] = len(consolidated)
            consolidated.append(rule)
        else:
            # Duplicate — merge targets and companion_resources into first
            first = consolidated[seen[rid]]

            # Merge targets
            new_targets = rule.get("targets", [])
            if new_targets:
                if "targets" not in first:
                    first["targets"] = []
                first["targets"].extend(new_targets)

            # Merge companion_resources (deduplicate by type)
            new_cr = rule.get("companion_resources")
            if new_cr:
                existing_cr = first.get("companion_resources", [])
                first["companion_resources"] = _dedup_companion_resources(existing_cr, new_cr)

    return consolidated, duplicates


def process_file(filepath: Path, apply: bool) -> dict[str, int] | None:
    """Process a single policy file. Returns duplicate info or None if clean."""
    with open(filepath) as f:
        data = yaml.safe_load(f)

    if not data or "rules" not in data:
        return None

    consolidated_rules, duplicates = consolidate_rules(data["rules"])

    if not duplicates:
        return None

    if apply:
        data["rules"] = consolidated_rules
        output = _dump_yaml(data)
        filepath.write_text(output)

    return duplicates


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate duplicate rule IDs in policy YAML files")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    parser.add_argument(
        "--dir",
        default="/Users/joshua/projects/microsoft/poc/azext-prototype/azext_prototype/governance/policies",
        help="Root directory to scan",
    )
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    mode = "APPLYING" if args.apply else "DRY-RUN"
    print(f"{'=' * 80}")
    print(f"DUPLICATE RULE CONSOLIDATION ({mode})")
    print(f"{'=' * 80}")

    total_files = 0
    total_duplicate_ids = 0
    total_removed_entries = 0

    for filepath in sorted(root.rglob("*.policy.yaml")):
        duplicates = process_file(filepath, apply=args.apply)
        if not duplicates:
            continue

        total_files += 1
        rel_path = filepath.relative_to(root)
        print(f"\n{'File: ' + str(rel_path)}")
        print("-" * 60)

        for rule_id, count in sorted(duplicates.items()):
            total_duplicate_ids += 1
            removed = count - 1
            total_removed_entries += removed
            action = "consolidated" if args.apply else "would consolidate"
            print(f"  {rule_id}: {count} entries -> {action} into 1 (removing {removed} duplicates)")

    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {total_duplicate_ids} duplicate rule IDs across {total_files} files")
    print(f"         {total_removed_entries} redundant entries {'removed' if args.apply else 'to remove'}")
    print(f"{'=' * 80}")

    if not args.apply and total_files > 0:
        print(f"\nRun with --apply to write changes.")


if __name__ == "__main__":
    main()
