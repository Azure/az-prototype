#!/usr/bin/env python3
"""Pre-compute neural embeddings for built-in governance policy rules.

Run at build time (before wheel construction) to generate
``azext_prototype/governance/policies/policy_vectors.json``.
This file is shipped inside the wheel so that runtime retrieval
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

OUTPUT_PATH = ROOT / "azext_prototype" / "governance" / "policies" / "policy_vectors.json"
MODEL_NAME = "all-MiniLM-L6-v2"


def main() -> None:
    from sentence_transformers import SentenceTransformer

    from azext_prototype.governance.policies import PolicyEngine

    # Load all built-in policies
    engine = PolicyEngine()
    engine.load()
    policies = engine.list_policies()

    if not policies:
        print("WARNING: No policies found. Generating empty vectors file.")
        OUTPUT_PATH.write_text(json.dumps({"model": MODEL_NAME, "dimension": 384, "rules": []}, indent=2))
        return

    # Extract rules with metadata
    rules_data: list[dict] = []
    for policy in policies:
        category = getattr(policy, "category", "")
        policy_name = getattr(policy, "name", "")
        services = getattr(policy, "services", [])
        for rule in getattr(policy, "rules", []):
            rule_id = getattr(rule, "id", "")
            severity = getattr(rule, "severity", "recommended")
            description = getattr(rule, "description", "")
            rationale = getattr(rule, "rationale", "")
            applies_to = getattr(rule, "applies_to", [])

            # Build the text used for embedding (matches PolicyIndex.text_for_embedding)
            text_parts = [
                f"[{category}] {policy_name}",
                f"Rule {rule_id} ({severity}): {description}",
            ]
            if rationale:
                text_parts.append(f"Rationale: {rationale}")
            if services:
                text_parts.append(f"Services: {', '.join(services)}")
            text = " ".join(text_parts)

            rules_data.append(
                {
                    "rule_id": rule_id,
                    "policy_name": policy_name,
                    "category": category,
                    "severity": severity,
                    "description": description,
                    "rationale": rationale,
                    "services": services,
                    "applies_to": applies_to,
                    "text": text,
                }
            )

    # Compute embeddings
    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    texts = [r["text"] for r in rules_data]
    print(f"Computing embeddings for {len(texts)} policy rules...")
    embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)

    dimension = embeddings.shape[1]
    for i, rule in enumerate(rules_data):
        rule["vector"] = embeddings[i].tolist()

    # Write output
    output = {
        "model": MODEL_NAME,
        "dimension": dimension,
        "rules": rules_data,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))

    print(f"Wrote {OUTPUT_PATH} ({len(rules_data)} rules, dimension={dimension})")


if __name__ == "__main__":
    main()
