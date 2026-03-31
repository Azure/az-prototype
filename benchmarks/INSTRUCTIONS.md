# Benchmark Testing Instructions

This document describes how to run benchmark tests comparing AI-generated code quality between GitHub Copilot (GHCP) and Claude Code. The process produces two HTML reports per run.

---

## Prerequisites

- A project configured with `az prototype init`
- A completed design (`az prototype design`)
- Access to GitHub Copilot via `az prototype build`
- Access to Claude Code for submitting prompts
- A debug log from the build run (enable with `--debug`)

---

## Step 1: Run the Build (GitHub Copilot)

```bash
az prototype build --debug 2>&1 | tee debug_$(date +%Y%m%d%H%M%S).log
```

This produces Terraform/Bicep/app code for all stages via GitHub Copilot. The debug log captures all AI prompts and responses.

---

## Step 2: Extract Stage Prompts and Responses

Create a comparison folder and extract inputs/responses from the debug log:

```bash
mkdir -p COMPARE
```

Write a Python extraction script (or use the manual process below) to extract from the debug log:
- For each stage N: find `"Stage N task prompt"` → extract `task_full=...` content → save as `COMPARE/INPUT_N.md`
- For each stage N: find `"Stage N response"` → extract `content_full=...` content → save as `COMPARE/CP_RESPONSE_N.md`

Content boundaries: each multi-line value starts after `=` on the marker line and continues until the next line matching `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \|` (a timestamp-prefixed log entry).

### Extraction Script Template

```python
#!/usr/bin/env python3
"""Extract stage prompts and responses from debug log."""
import re, os, sys

LOG = sys.argv[1]  # Path to debug log
OUT = sys.argv[2] if len(sys.argv) > 2 else "COMPARE"
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \|")

with open(LOG, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

def find_line(pattern, start=0):
    for i in range(start, len(lines)):
        if pattern in lines[i]:
            return i
    return -1

def extract_content(start_line, prefix):
    first_line = lines[start_line]
    idx = first_line.find(prefix + "=")
    if idx == -1:
        return ""
    parts = [first_line[idx + len(prefix) + 1:]]
    for i in range(start_line + 1, len(lines)):
        if TIMESTAMP_RE.match(lines[i]):
            break
        parts.append(lines[i])
    return "".join(parts)

os.makedirs(OUT, exist_ok=True)
for stage_num in range(1, 50):
    prompt_line = find_line(f"Stage {stage_num} task prompt")
    if prompt_line == -1:
        break
    response_line = find_line(f"Stage {stage_num} response", prompt_line)
    if response_line == -1:
        break
    task_full_line = next((i for i in range(prompt_line, min(prompt_line+10, len(lines)))
                          if "task_full=" in lines[i]), -1)
    content_full_line = next((i for i in range(response_line, min(response_line+10, len(lines)))
                             if "content_full=" in lines[i]), -1)
    if task_full_line == -1 or content_full_line == -1:
        continue
    prompt = extract_content(task_full_line, "task_full")
    response = extract_content(content_full_line, "content_full")
    with open(os.path.join(OUT, f"INPUT_{stage_num}.md"), "w") as f:
        f.write(prompt)
    with open(os.path.join(OUT, f"CP_RESPONSE_{stage_num}.md"), "w") as f:
        f.write(response)
    print(f"Stage {stage_num}: INPUT={len(prompt)}B  CP_RESPONSE={len(response)}B")
```

Usage: `python3 extract.py debug_20260328024351.log COMPARE`

---

## Step 3: Generate Claude Code Responses

For each extracted INPUT file, submit it to Claude Code and save the response:

```
COMPARE/INPUT_1.md  →  submit to Claude Code  →  COMPARE/C_RESPONSE_1.md
COMPARE/INPUT_2.md  →  submit to Claude Code  →  COMPARE/C_RESPONSE_2.md
...
COMPARE/INPUT_N.md  →  submit to Claude Code  →  COMPARE/C_RESPONSE_N.md
```

**Consistency requirements:**
- Use the same model for all stages (do not switch models mid-test)
- Submit each INPUT as a single prompt (do not split or summarize)
- Do not add extra instructions beyond what's in the INPUT
- Record the model name and version used
- Note the effort/reasoning level if configurable

---

## Step 4: Score Both Response Sets

For each stage, score both CP_RESPONSE and C_RESPONSE against the 14 benchmarks defined in [README.md](README.md). Use the scoring guide for each sub-criterion.

### Per-Stage Scoring Process

For each stage N:
1. Read `INPUT_N.md` to understand requirements
2. Read `CP_RESPONSE_N.md` and `C_RESPONSE_N.md`
3. For each of the 14 benchmarks, evaluate both responses
4. Score each sub-criterion using the weight and scoring guide
5. Sum sub-criteria scores for the benchmark total (0-100)
6. Record scores in a structured format

### Benchmarks Applicable by Stage Type

| Stage Type | Applicable Benchmarks |
|------------|----------------------|
| Infrastructure (IaC) | All 14: B-INST, B-CNST, B-TECH, B-SEC, B-OPS, B-DEP, B-SCOPE, B-QUAL, B-OUT, B-CONS, B-DOC, B-REL, B-RBAC, B-ANTI |
| Application code | B-INST, B-CNST, B-TECH, B-SEC, B-QUAL, B-OUT, B-CONS, B-DOC, B-REL, B-ANTI (skip B-OPS, B-DEP, B-SCOPE, B-RBAC) |
| Documentation | B-INST, B-DOC, B-REL, B-QUAL, B-OUT, B-CONS, B-SCOPE (skip B-TECH, B-SEC, B-DEP, B-OPS, B-RBAC, B-ANTI, B-CNST) |

---

## Step 5: Generate Reports

### Copy-Paste Analysis Instructions

After collecting all INPUT, CP_RESPONSE, and C_RESPONSE files in the COMPARE folder, use the following instructions to generate the benchmark comparison reports. Copy and paste the text below (between the `---` markers) into your analysis environment:

---

**BEGIN ANALYSIS INSTRUCTIONS**

You have access to a folder called COMPARE/ containing files for a multi-stage AI code generation benchmark comparison:

- `INPUT_N.md` — The prompt/requirements for stage N
- `CP_RESPONSE_N.md` — GitHub Copilot's response for stage N
- `C_RESPONSE_N.md` — Claude Code's response for stage N

Read the benchmark definitions from `benchmarks/README.md` in the project root.

For EACH stage (1 through the highest N found):

1. Read all three files (INPUT, CP_RESPONSE, C_RESPONSE)
2. Score BOTH responses against all applicable benchmarks (see README.md for scoring rubrics)
3. For each benchmark sub-criterion, apply the weighted scoring guide exactly as documented
4. Record specific findings — quote code snippets that demonstrate compliance or violations
5. Note the winner for each benchmark dimension

Then produce TWO output files:

### File 1: `benchmarks/YYYY-MM-DD-HH-mm-ss.html`

Use `benchmarks/TEMPLATE.html` as the base. Copy the template and populate the DATA section
with the actual run data. The template has a fixed rendering engine — only the data arrays
need to change between runs. The layout, styling, and tab structure are handled automatically.

Data arrays to populate in the template:
- `META` — date, project, model, summary
- `benchmarks[]` — 14 benchmark scores (ghcp and comp values for each)
- `stages[]` — per-stage data with `dims[]` (dimension comparison rows) and `notes` (analysis narrative)
- `patterns{}` — systematic strengths/weaknesses (ghcpStrengths, ccStrengths, ghcpWeaknesses, ccWeaknesses)
- `bugs[]` — critical bugs with tool, stages, severity
- `heatmapData[]` — dimension winners grid across all stages
- `verdictParagraphs[]` — HTML paragraphs for the final verdict section

The template automatically renders:
- **Overview tab**: Benchmark scores table, aggregate stage scores, systematic strengths/weaknesses (4 cards), critical bugs table, dimension winners heatmap, and final verdict with paragraph layout
- **One tab per stage**: Score bars, dimension comparison table (Dimension | Copilot | Claude Code | Winner), and analysis notes

The dimensions in each stage's `dims[]` array are flexible — they change based on
what is relevant to that stage (IaC dimensions differ from documentation dimensions).
The rendering engine handles any number of dimensions per stage.

### File 2: `benchmarks/overall.html` (create or update)

A trends dashboard HTML file using Tailwind CSS and Chart.js (CDN: `https://cdn.jsdelivr.net/npm/chart.js`).

Structure:
- **Current benchmarks** section: Table showing current scores for all 14 benchmarks for both tools
- **Trends over time** section: Line charts (one per benchmark) showing score changes across runs. X-axis = run date, Y-axis = score (0-100). Two lines per chart (GHCP in blue, Claude Code in orange).
- **Aggregate trend** chart: Overall average score over time for both tools
- **Significant variances** section: Auto-detect and highlight any benchmark that changed by more than 10 points between consecutive runs
- **Run history** table: Date, model, project name, overall GHCP score, overall Claude Code score, winner, for each historical run

If `benchmarks/overall.html` already exists, parse the existing historical data from it and append the new run's data. If it does not exist, create it with the current run as the first data point.

Data format for historical tracking: embed a `<script>` block with a `BENCHMARK_HISTORY` JavaScript array containing objects with: `date`, `model`, `project`, `ghcp_scores` (object with benchmark IDs as keys), `comparison_scores` (same structure), `stage_scores` (per-stage detail).

**END ANALYSIS INSTRUCTIONS**

---

## Consistency Guidelines

To ensure reproducible results across benchmark runs:

1. **Same project**: Use the same project configuration and design for comparable runs
2. **Same model**: Record the exact model name and version for both tools
3. **Same effort level**: Note effort/reasoning configuration if applicable
4. **Same extraction process**: Use the same debug log extraction script
5. **Fresh context**: Start each Claude Code session with a clean context (no prior conversation)
6. **Complete submission**: Submit the full INPUT — do not truncate or summarize
7. **Single-shot**: Each stage should be a single prompt-response pair (no follow-ups or clarifications)

## Report Generation Rules

### What is generated automatically during a benchmark run:
- `benchmarks/YYYY-MM-DD-HH-mm-ss.html` — individual run report (always generated)

### What is NOT generated automatically:
- `benchmarks/overall.html` — trends dashboard. Only update when explicitly instructed.
- PDF report from `benchmarks/TEMPLATE.docx` — only generate when explicitly instructed.

Individual benchmark run files may be generated for testing purposes and may be deleted
at any time. Do not assume a run file should be incorporated into the overall dashboard
or PDF unless explicitly told to do so.

### When instructed to update the overall report and generate a PDF:
1. Parse the designated benchmark run HTML file(s) for score data
2. Update `benchmarks/overall.html` by appending the new run data to `BENCHMARK_HISTORY`
3. Use `scripts/generate_pdf.py` as a reference (or run it directly) to:
   a. Generate all charts using matplotlib (overall trend, per-benchmark factor
      comparison charts, per-benchmark score trend charts)
   b. Use python-docx to populate `benchmarks/TEMPLATE.docx` with:
      - Extension version (from `setup.py: VERSION`)
      - Benchmark run date (cover + footer)
      - All score tables (benchmark scores, sub-factor breakdowns, improvement areas)
      - Embed generated chart images at each `[Insert ...]` placeholder
      - Populate the conclusion section
   c. Save the populated document as `benchmarks/YYYY-MM-DD_Benchmark_Report.docx`
4. Charts are generated programmatically. Do NOT leave chart placeholders for manual
   insertion. All 29 charts (1 overall + 14 factor + 14 trend) must be embedded.
5. Convert the DOCX to PDF using `docx2pdf` (launches Word automatically on macOS).
   Output: `benchmarks/YYYY-MM-DD_Benchmark_Report.pdf`
6. Delete the temporary DOCX after PDF conversion.

## Report File Naming

- Per-run reports: `benchmarks/YYYY-MM-DD-HH-mm-ss.html` (timestamp of the build run)
- Overall dashboard: `benchmarks/overall.html` (updated only when instructed)
- PDF template: `benchmarks/TEMPLATE.docx` (populated and exported only when instructed)
- Comparison data: `COMPARE/` folder (can be archived after report generation)

## Interpreting Results

| Rating | Score Range | Action |
|--------|------------|--------|
| Excellent (90-100) | No action needed | Monitor for regressions |
| Good (75-89) | Minor improvements possible | Review specific sub-criteria |
| Acceptable (60-74) | Notable gaps exist | Prioritize improvements |
| Poor (40-59) | Significant issues | Investigate root causes in prompt pipeline |
| Failing (0-39) | Critical failure | Immediate investigation required |

**Regression threshold**: Any benchmark dropping more than 5 points between runs warrants investigation. Drops of 10+ points are flagged as significant variances in the overall dashboard.
