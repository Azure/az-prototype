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
    # Extract post-transform output (final quality after governance transforms)
    transform_line = find_line(f"Stage {stage_num} post-transform", prompt_line)
    task_full_line = next((i for i in range(prompt_line, min(prompt_line+10, len(lines)))
                          if "task_full=" in lines[i]), -1)
    transformed_full_line = -1
    if transform_line != -1:
        transformed_full_line = next((i for i in range(transform_line, min(transform_line+10, len(lines)))
                                     if "transformed_full=" in lines[i]), -1)
    # Fallback to raw response if no post-transform entry (e.g., no transforms applied)
    if transformed_full_line == -1:
        response_line = find_line(f"Stage {stage_num} response", prompt_line)
        if response_line != -1:
            transformed_full_line = next((i for i in range(response_line, min(response_line+10, len(lines)))
                                         if "content_full=" in lines[i]), -1)
            content_key = "content_full"
        else:
            continue
    else:
        content_key = "transformed_full"
    if task_full_line == -1 or transformed_full_line == -1:
        continue
    prompt = extract_content(task_full_line, "task_full")
    response = extract_content(transformed_full_line, content_key)
    with open(os.path.join(OUT, f"INPUT_{stage_num}.md"), "w") as f:
        f.write(prompt)
    with open(os.path.join(OUT, f"CP_RESPONSE_{stage_num}.md"), "w") as f:
        f.write(response)
    print(f"Stage {stage_num}: INPUT={len(prompt)}B  CP_RESPONSE={len(response)}B (source: {content_key})")