# AI Code Generation Benchmark Suite

This benchmark suite measures the quality of AI-generated code across infrastructure-as-code (IaC), application scaffolding, and documentation. It is **project-agnostic** and can be applied to any multi-stage build pipeline regardless of cloud provider, IaC tool, or programming language.

## Benchmark Testing Workflow

1. Run `az prototype build` via GitHub Copilot to produce stage prompts (INPUTs) and responses (CP_RESPONSEs)
2. Extract INPUTs and CP_RESPONSEs from the debug log
3. Submit each INPUT to Claude Code to produce a second set of responses (C_RESPONSEs)
4. Score both response sets against the 14 benchmarks below
5. Generate HTML reports using INSTRUCTIONS.md

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for detailed testing methodology, scoring rubrics, and report generation steps.

---

## 14 Benchmarks

### B-INST: Instruction Adherence (0-100)

**Does the output implement exactly what was requested — no more, no less?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Required resources/components present | 30 | `(items present / items requested) * 30`. Each missing item deducts proportionally. |
| No unrequested additions | 25 | Start at 25. Deduct 5 per unrequested resource, dependency, or feature added. Min 0. |
| Configuration values match specification | 20 | `(correct config values / total specified values) * 20`. Check SKUs, retention, sizes, etc. |
| Output format compliance | 15 | File structure, naming, fenced code block labels match instructions. Binary: 15 (all correct) or deduct 3 per deviation. |
| Architectural intent preserved | 10 | Upstream/downstream integration points match spec. 10 if correct, 5 if partial, 0 if wrong. |

---

### B-CNST: Constraint and Directive Compliance (0-100)

**Are NEVER/MUST/CRITICAL directives and governance policies followed?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| NEVER directive compliance | 35 | Binary per directive: any single NEVER violation = 0 for that directive. `(directives followed / total NEVER directives) * 35`. |
| MUST directive compliance | 30 | `(MUST directives satisfied / total MUST directives) * 30`. |
| Conditional rule compliance | 15 | `(triggered conditions correctly handled / total triggered conditions) * 15`. |
| Prohibition override resistance | 10 | 10 if no contextual cue overrides an explicit prohibition. 0 if any prohibition overridden by context. |
| Constraint consistency across outputs | 10 | Same constraints applied uniformly across all generated files. 10 if consistent, 5 if mostly, 0 if inconsistent. |

---

### B-TECH: Technical Correctness (0-100)

**Is the generated code syntactically valid, correctly versioned, and would it deploy without errors?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Syntactic validity | 25 | Per-file: parseable without errors = full marks. Any syntax error = 0 for that file. Average across files. |
| API/SDK version correctness | 25 | `(correct versions / total version references) * 25`. Mandatory versions must match exactly. |
| Reference integrity | 20 | All variables, locals, data sources, outputs, and module references resolve. `(resolvable / total) * 20`. |
| Provider/dependency consistency | 15 | Declared provider versions match syntax patterns used. 15 if all consistent, 0 if v1/v2 mismatch. |
| Runtime viability | 15 | Reviewer judgment: would `terraform apply` / `dotnet build` / `npm install` succeed? 15/10/5/0 scale. |

---

### B-SEC: Security Posture (0-100)

**Are security best practices followed across authentication, encryption, network exposure, and secrets?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Authentication method | 25 | 25 for managed identity / RBAC everywhere. Deduct 10 per connection string, 15 per hardcoded key. Min 0. |
| Secrets hygiene | 25 | 25 if zero sensitive values in outputs/env/code. Deduct 10 per exposed secret. Min 0. |
| Network exposure controls | 20 | 20 if public access disabled where mandated. Deduct 10 per public endpoint that should be private. |
| Encryption configuration | 15 | TLS 1.2+, TDE/SSE enabled, infrastructure encryption where applicable. `(correct / total) * 15`. |
| Least-privilege RBAC scoping | 15 | 15 if narrowest built-in roles used with correct principal separation (admin to deployer, data to app MI). Deduct 5 per over-permissive assignment. |

---

### B-OPS: Operational Readiness (0-100)

**Are deployment scripts complete, functional, and production-grade?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Argument parsing | 25 | 25 if --dry-run, --destroy, --help all present and functional. Deduct 8 per missing flag. |
| Error handling | 20 | 20 if `set -euo pipefail` + trap + exit codes all present. Deduct 7 per missing element. |
| Pre-flight validation | 20 | 20 if login check + tool availability + upstream dependency validation all present. Deduct 7 per missing check. |
| Post-deployment verification | 20 | 20 if script verifies resource state via CLI/API after deploy. 10 if basic output check only. 0 if absent. |
| Output export | 15 | 15 if outputs exported to deterministic JSON file path. 0 if missing. |

---

### B-DEP: Dependency and Provider Hygiene (0-100)

**Are declared dependencies minimal, correct, and consistently versioned?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| No unnecessary dependencies | 30 | 30 if every declared provider/package is used. Deduct 10 per unused dependency. Min 0. |
| Version pinning consistency | 25 | 25 if all instances of same dependency use same version. 0 if version mismatch (e.g., v1 in one stage, v2 in another). |
| Dependency-syntax alignment | 20 | 20 if declared version matches syntax patterns (e.g., v2.x syntax with v2.x provider). 0 if mismatch. |
| Minimal provider surface | 15 | 15 if fewest possible providers used. Deduct 5 per provider that could be eliminated. |
| Backend configuration consistency | 10 | 10 if same backend type across all stages. 0 if mixed. |

---

### B-SCOPE: Scope Discipline (0-100)

**Does the output stay within the boundaries defined by the input specification?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| No unrequested resources created | 35 | 35 if all resources match service list. Deduct 7 per unrequested resource (extra subnets, firewall rules, etc.). Min 0. |
| No speculative infrastructure | 25 | 25 if no resources added "for future use" or "just in case." Deduct 10 per speculative addition. |
| Companion resources match architecture | 20 | 20 if PE/DNS/diagnostics created only where mandated by policy and in the correct stage. 0 if circular dependencies created. |
| No dead code or unused references | 10 | 10 if no declared-but-unused remote state, data sources, or variables. Deduct 3 per dead reference. |
| Variable/output surface proportional | 10 | 10 if variable and output counts are proportional to resources. Deduct 3 per clearly redundant output (e.g., individual DNS zone IDs alongside a map). |

---

### B-QUAL: Code Quality and Readability (0-100)

**Is the code well-organized, properly named, and maintainable?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| File organization | 25 | Resources in correct files per specification. 25 if perfect, deduct 5 per misplaced resource block. |
| Naming convention adherence | 20 | `(correctly named resources / total resources) * 20`. Check against prescribed naming pattern. |
| Idiomatic patterns | 20 | Uses language/tool best practices (for_each, locals, data sources, map outputs). 20/15/10/5/0 scale. |
| Variable validation | 15 | 15 if critical variables have validation blocks (SKUs, ranges, regex). 10 if partial. 0 if none. |
| Comment quality | 20 | Inline comments explain key decisions (not trivial). Design notes section present. 20/15/10/5/0 scale. |

---

### B-OUT: Output Interface Completeness (0-100)

**Are all required outputs/exports properly defined for downstream consumers?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Downstream-referenced values exported | 35 | `(required outputs present / total required outputs) * 35`. Check against INPUT's downstream list. |
| No sensitive values in outputs | 20 | 20 if zero keys/passwords/connection strings in outputs. 0 if any sensitive value exposed. |
| Output naming consistency | 20 | 20 if same naming pattern across all stages (e.g., always `*_id`, `*_name`). Deduct 5 per inconsistency. |
| Endpoint/FQDN exports | 15 | 15 if service endpoints available for app configuration. 0 if missing when needed. |
| Identity exports | 10 | 10 if principal_id, client_id exported when RBAC/MI involved. 0 if missing. |

---

### B-CONS: Cross-Stage Consistency (0-100)

**Are patterns, conventions, and decisions uniform across all stages?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Provider version consistency | 25 | 25 if identical provider versions across all stages. 0 if any version differs. |
| Backend configuration uniformity | 20 | 20 if same backend type and pattern everywhere. 0 if mixed (e.g., local in some, azurerm in others). |
| Tag placement pattern uniformity | 20 | 20 if tags in same position (top-level or body) across all stages. 0 if mixed. |
| Naming pattern uniformity | 20 | 20 if same locals/naming structure across all stages. Deduct 5 per structural deviation. |
| Remote state reference pattern | 15 | 15 if same mechanism for cross-stage references everywhere. 0 if inconsistent. |

---

### B-DOC: Documentation Quality (0-100)

**Are generated documents complete, accurate, and actionable?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Completeness | 25 | `(sections present / required sections) * 25`. Check: architecture, deployment, troubleshooting, CI/CD. |
| Accuracy | 25 | Spot-check 10+ facts (resource names, roles, stages) against generated code. `(correct / checked) * 25`. |
| Actionability | 20 | Commands, paths, and variable references work if copy-pasted. `(working commands / total commands) * 20`. |
| Structural quality | 15 | 15 if proper headings, ToC, diagrams, tables, logical flow. Deduct 3 per structural issue. |
| No truncation or context loss | 15 | 15 if document is complete. 0 if mid-stream abort, "I don't have context", or any truncation. |

---

### B-REL: Response Reliability (0-100)

**Is the AI response complete, parseable, and free of structural failures?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Response completeness | 30 | 30 if all requested files present and complete. 0 if any file missing or truncated. |
| Parseable output | 25 | 25 if all fenced code blocks are valid and extractable with correct filename labels. Deduct 8 per unparseable block. |
| No hallucinated functions/APIs | 25 | 25 if all referenced functions, APIs, and builtins exist. Deduct 5 per hallucinated reference. |
| Token efficiency | 20 | 20 if response is concise and meaningful. Deduct 5 for excessive boilerplate/repetition. Min 0. |

---

### B-RBAC: RBAC and Identity Architecture (0-100)

**Are identity, role assignment, and principal separation patterns correct?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| Correct RBAC mechanism per service | 30 | 30 if correct mechanism used (ARM RBAC, Cosmos sqlRoleAssignments, SQL T-SQL, etc.). 0 if wrong layer. |
| Deterministic assignment names | 20 | 20 if `uuidv5()` with deterministic seeds. 10 if deterministic but different method. 0 if `uuid()`. |
| Principal separation | 20 | 20 if admin roles to deployer and data roles to app MI. 0 if admin roles to app MI. |
| Least-privilege role selection | 15 | 15 if narrowest built-in role for each use case. Deduct 5 per over-permissive role. |
| principalType annotation | 15 | 15 if `ServicePrincipal` for MI, `User` for humans. 0 if missing or incorrect. |

---

### B-ANTI: Anti-Pattern Absence (0-100)

**Are known bad patterns absent from all generated output?**

| Sub-criterion | Weight | Scoring Guide |
|---------------|--------|---------------|
| No credentials in code/config/outputs | 25 | 25 if zero hardcoded keys, passwords, or secrets. 0 if any found. |
| No overly permissive network rules | 20 | 20 if no `0.0.0.0/0`, no public access where disabled is required. Deduct 10 per violation. |
| No deprecated/incorrect syntax | 20 | 20 if no v1 syntax with v2 provider, no `jsondecode` on v2 output, etc. Deduct 5 per occurrence. |
| No hardcoded upstream resource names | 20 | 20 if all cross-stage references via variables/remote state. 0 if any upstream name hardcoded. |
| No incomplete/truncated scripts | 15 | 15 if all scripts syntactically complete. 0 if any script truncated or unclosed. |

---

## Scoring Summary

Each benchmark is scored 0-100. The **overall score** is the unweighted average of all 14 benchmarks. Per-stage scores are calculated by averaging applicable benchmarks for that stage (documentation stages skip B-TECH, B-SEC, B-DEP, B-RBAC, B-ANTI and score only B-INST, B-DOC, B-REL, B-QUAL, B-OUT, B-CONS, B-OPS, B-SCOPE).

**Severity tiers**: `CRITICAL` > `NEVER/MUST` > `SHOULD/RECOMMENDED`

| Rating | Score Range | Description |
|--------|------------|-------------|
| Excellent | 90-100 | Production-ready, minimal or no issues |
| Good | 75-89 | Functional with minor issues |
| Acceptable | 60-74 | Works but has notable gaps |
| Poor | 40-59 | Significant issues, needs rework |
| Failing | 0-39 | Critical failures, unusable |

## Report Generation Policy

Individual benchmark run reports (`YYYY-MM-DD-HH-mm-ss.html`) may be generated at any time
for testing and measurement purposes. These files may be deleted without consequence.

The following are only generated or updated when **explicitly instructed**:
- `overall.html` (trends dashboard)
- PDF export from `TEMPLATE.docx`

Do not automatically update the trends dashboard or generate a PDF after a benchmark run.
