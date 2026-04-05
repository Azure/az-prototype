#!/usr/bin/env python3
"""Pre-compute neural embeddings for built-in governance rules.

Run at build time (before wheel construction) to generate three vector
files in ``azext_prototype/governance/``:

- ``policy.vectors.json``
- ``anti-pattern.vectors.json``
- ``standard.vectors.json``

These files are shipped inside the wheel so that runtime retrieval
uses pure-Python cosine similarity — no ``torch`` or
``sentence-transformers`` needed on the user's machine.

Usage::

    python scripts/compute_embeddings.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the package is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOVERNANCE_DIR = ROOT / "azext_prototype" / "governance"
MODEL_NAME = "all-MiniLM-L6-v2"


def _compute_and_write(items: list[dict], output_path: Path, model: object) -> None:
    """Compute embeddings for *items* and write to *output_path*."""
    if not items:
        print(f"  WARNING: No items found. Writing empty vectors file to {output_path.name}")
        output_path.write_text(json.dumps({"model": MODEL_NAME, "dimension": 384, "items": []}, indent=2))
        return

    texts = [r["text"] for r in items]
    print(f"  Computing embeddings for {len(texts)} items...")
    embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)  # type: ignore[attr-defined]

    dimension = embeddings.shape[1]
    for i, item in enumerate(items):
        item["vector"] = embeddings[i].tolist()

    output = {
        "model": MODEL_NAME,
        "dimension": dimension,
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))
    print(f"  Wrote {output_path.name} ({len(items)} items, dimension={dimension})")


def _build_policy_items() -> list[dict]:
    """Extract policy rules with metadata for embedding."""
    from azext_prototype.governance.policies import PolicyEngine

    engine = PolicyEngine()
    engine.load()
    policies = engine.list_policies()

    items: list[dict] = []
    for policy in policies:
        domain = getattr(policy, "domain", "")
        policy_name = getattr(policy, "name", "")
        services = getattr(policy, "services", [])
        for rule in getattr(policy, "rules", []):
            rule_id = getattr(rule, "id", "")
            severity = getattr(rule, "severity", "recommended")
            description = getattr(rule, "description", "")
            rationale = getattr(rule, "rationale", "")
            applies_to = getattr(rule, "applies_to", [])

            text_parts = [
                f"[{domain}] {policy_name}",
                f"Rule {rule_id} ({severity}): {description}",
            ]
            if rationale:
                text_parts.append(f"Rationale: {rationale}")
            if services:
                text_parts.append(f"Services: {', '.join(services)}")

            items.append(
                {
                    "kind": "policy",
                    "item_id": rule_id,
                    "domain": domain,
                    "severity": severity,
                    "description": description,
                    "rationale": rationale,
                    "services": services,
                    "applies_to": applies_to,
                    "text": " ".join(text_parts),
                }
            )
    return items


def _build_anti_pattern_items() -> list[dict]:
    """Extract anti-pattern checks for embedding."""
    from azext_prototype.governance.anti_patterns import load as load_anti_patterns

    checks = load_anti_patterns()
    items: list[dict] = []
    for check in checks:
        text_parts = [
            f"[{check.domain}] Anti-pattern {check.id}",
            check.warning_message,
        ]
        items.append(
            {
                "kind": "anti-pattern",
                "item_id": check.id,
                "domain": check.domain,
                "description": check.warning_message,
                "applies_to": check.applies_to,
                "text": " ".join(text_parts),
            }
        )
    return items


def _build_standard_items() -> list[dict]:
    """Extract standard principles for embedding."""
    from azext_prototype.governance.standards import load as load_standards

    standards = load_standards()
    items: list[dict] = []
    for std in standards:
        for principle in std.principles:
            text_parts = [
                f"[{std.domain}] Standard {principle.id}",
                principle.description,
            ]
            if principle.rationale:
                text_parts.append(f"Rationale: {principle.rationale}")
            items.append(
                {
                    "kind": "standard",
                    "item_id": principle.id,
                    "domain": std.domain,
                    "description": principle.description,
                    "rationale": getattr(principle, "rationale", ""),
                    "applies_to": principle.applies_to,
                    "text": " ".join(text_parts),
                }
            )
    return items


def main() -> None:
    from sentence_transformers import SentenceTransformer

    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    print("\n--- Policies ---")
    policy_items = _build_policy_items()
    _compute_and_write(policy_items, GOVERNANCE_DIR / "policy.vectors.json", model)

    print("\n--- Anti-Patterns ---")
    ap_items = _build_anti_pattern_items()
    _compute_and_write(ap_items, GOVERNANCE_DIR / "anti-pattern.vectors.json", model)

    print("\n--- Standards ---")
    std_items = _build_standard_items()
    _compute_and_write(std_items, GOVERNANCE_DIR / "standard.vectors.json", model)

    print("\nDone.")


if __name__ == "__main__":
    main()
