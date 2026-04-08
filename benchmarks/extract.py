#!/usr/bin/env python3
"""Extract stage prompts and responses from debug log.

When a stage has a full retry (second ``task prompt`` entry), uses the
**last** task prompt and the final post-transform output for that stage.
This ensures benchmarks measure the retry attempt's input — the one that
includes prior QA findings — rather than the original attempt.
"""
import os
import re
import sys

LOG = sys.argv[1]  # Path to debug log
OUT = sys.argv[2] if len(sys.argv) > 2 else "COMPARE"
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \|")

with open(LOG, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()


def find_all_lines(pattern: str) -> list[int]:
    """Return line indices of ALL occurrences of *pattern*."""
    return [i for i, line in enumerate(lines) if pattern in line]


def find_line(pattern: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if pattern in lines[i]:
            return i
    return -1


def extract_content(start_line: int, prefix: str) -> str:
    first_line = lines[start_line]
    idx = first_line.find(prefix + "=")
    if idx == -1:
        return ""
    parts = [first_line[idx + len(prefix) + 1 :]]
    for i in range(start_line + 1, len(lines)):
        if TIMESTAMP_RE.match(lines[i]):
            break
        parts.append(lines[i])
    return "".join(parts)


os.makedirs(OUT, exist_ok=True)

for stage_num in range(1, 50):
    # Find ALL task prompt entries for this stage — use the LAST one
    # (if a full retry happened, the second prompt includes prior QA findings)
    all_prompts = find_all_lines(f"Stage {stage_num} task prompt")
    if not all_prompts:
        break

    prompt_line = all_prompts[-1]  # Use last (retry) attempt
    retried = len(all_prompts) > 1

    # Find task_full= within a few lines of the prompt marker
    task_full_line = next(
        (i for i in range(prompt_line, min(prompt_line + 15, len(lines))) if "task_full=" in lines[i]),
        -1,
    )

    # Find the LAST post-transform output after the last task prompt
    transform_line = find_line(f"Stage {stage_num} post-transform", prompt_line)
    # Walk forward to find the very last post-transform for this stage
    # (there may be multiple from QA remediation cycles)
    while True:
        next_transform = find_line(f"Stage {stage_num} post-transform", transform_line + 1)
        # Stop if we hit a different stage's task prompt or end of file
        next_stage_prompt = find_line(f"Stage {stage_num + 1} task prompt", transform_line + 1)
        if next_transform == -1:
            break
        if next_stage_prompt != -1 and next_transform > next_stage_prompt:
            break
        transform_line = next_transform

    transformed_full_line = -1
    content_key = "transformed_full"

    if transform_line != -1:
        transformed_full_line = next(
            (
                i
                for i in range(transform_line, min(transform_line + 15, len(lines)))
                if "transformed_full=" in lines[i]
            ),
            -1,
        )

    # Fallback to raw response if no post-transform entry
    if transformed_full_line == -1:
        response_line = find_line(f"Stage {stage_num} response", prompt_line)
        if response_line != -1:
            transformed_full_line = next(
                (
                    i
                    for i in range(response_line, min(response_line + 15, len(lines)))
                    if "content_full=" in lines[i]
                ),
                -1,
            )
            content_key = "content_full"
        else:
            continue

    if task_full_line == -1 or transformed_full_line == -1:
        continue

    prompt = extract_content(task_full_line, "task_full")
    response = extract_content(transformed_full_line, content_key)

    retry_tag = " [RETRY]" if retried else ""
    with open(os.path.join(OUT, f"INPUT_{stage_num}.md"), "w") as f:
        f.write(prompt)
    with open(os.path.join(OUT, f"CP_RESPONSE_{stage_num}.md"), "w") as f:
        f.write(response)
    print(
        f"Stage {stage_num}{retry_tag}: INPUT={len(prompt)}B  "
        f"CP_RESPONSE={len(response)}B (source: {content_key})"
    )
