# Plan: Vision/Wireframe Integration for Application Code Generation

## Problem

Design artifacts (wireframes, screenshots, mockups) uploaded during discovery are consumed by the AI during the discovery conversation but **discarded during state persistence**. They are not available to the build stage — the application architect and developer agents generate UI code based on text descriptions only, with no visual reference.

## Current State

### What works
- `parsers/binary_reader.py` extracts images from PDF, DOCX, PPTX (`EmbeddedImage` dataclass, lines 40-46)
- `stages/discovery.py` builds OpenAI vision content arrays (`_build_opening()`, lines 1182-1194)
- `AIMessage.content` supports `str | list` for multi-modal content (`ai/provider.py`, line 20)
- All 3 AI providers pass vision content through to the API unchanged
- `qa_engineer.py` has a working `execute_with_image()` method (lines 104-173)

### What's broken
1. **`discovery_state.py:401-415`** — `update_from_exchange()` strips base64 image data, replaces with `[N image(s) attached]` placeholder. Images are lost on persist.
2. **No artifact storage for images** — `AgentContext.artifacts` (base.py:70-92) stores text only. `design_stage.py:246` calls `context.add_artifact()` with text content. No `design_images` artifact.
3. **Build agents are text-only** — `application_architect.py:93-109`, `react_developer.py:87-100`, `csharp_developer.py:89-107` all use `AIMessage(role="system", content=text_string)`. None construct vision content arrays.
4. **`_execute_with_retry()` (build_session.py:3494)** passes `task` as `str`. No mechanism to pass image content alongside the task.

## Implementation Plan

### Phase 1: Persist design images during discovery

**File: `stages/discovery_state.py`**
- **Lines 384-431** — `update_from_exchange()`
- Add: save image data to `.prototype/state/design_images/` directory as individual files
- Store metadata in discovery state YAML: `design_images: [{source: "requirements.docx/image1.png", path: ".prototype/state/design_images/img_001.png", mime: "image/png"}, ...]`
- Keep the `[N image(s) attached]` placeholder in conversation text for readability
- Add `MAX_DESIGN_IMAGES = 50` cap to prevent storage bloat

**File: `stages/discovery.py`**
- After `_build_opening()` returns vision content (line 780), also save images to disk
- New method: `_persist_design_images(images: list[dict]) -> list[dict]` — writes base64 data to files, returns metadata list

### Phase 2: Load design images in build stage

**File: `stages/build_session.py`**
- In `__init__` (lines 165-200), load design image metadata from discovery state
- New method: `_load_design_images() -> list[dict]` — reads `.prototype/state/design_images/` and returns `[{path, mime, base64_data}, ...]`
- Store as `self._design_images: list[dict]`

**File: `agents/base.py`**
- `AgentContext.artifacts` (line 79) — no change needed, artifacts dict already accepts `Any` values
- Add artifact during build init: `context.add_artifact("design_images", design_image_list)`

### Phase 3: Wire vision into app-layer agent execution

**File: `stages/build_session.py`**
- In `_decompose_app_stage()` (lines ~1970-2020), when routing to a developer agent, check if `self._design_images` is populated
- If images exist, construct a vision-enabled task:
  ```python
  if self._design_images and layer == "app":
      task_content: list[dict] = [{"type": "text", "text": task}]
      for img in self._design_images:
          task_content.append({
              "type": "image_url",
              "image_url": {
                  "url": f"data:{img['mime']};base64,{img['data']}",
                  "detail": "high",
              },
          })
  ```
- Pass `task_content` (list) instead of `task` (str) to the agent

**File: `stages/build_session.py` — `_execute_with_retry()`**
- **Lines 3494-3534** — Change `task: str` parameter to `task: str | list`
- Pass through to `_execute_with_continuation()`

**File: `stages/build_session.py` — `_execute_with_continuation()`**
- **Lines 3540-3573** — Change `task: str` parameter to `task: str | list`
- Line 3559: `response = agent.execute(self._context, task)` — already works with both types if agent.execute() handles it

### Phase 4: Update agent execute() methods for vision

**File: `agents/base.py` — `execute()`**
- **Lines 163-204**
- Line 183: `messages.append(AIMessage(role="user", content=task))` — already works: `content` accepts `str | list`
- No change needed in base execute()

**File: `agents/builtin/application_architect.py` — `execute()`**
- **Lines 72-123**
- Line 114: `messages.append(AIMessage(role="user", content=task))` — already accepts list
- Add: check `context.get_artifact("design_images")` and inject into messages if present:
  ```python
  design_images = context.get_artifact("design_images")
  if design_images:
      messages.append(
          AIMessage(
              role="system",
              content="Design wireframes/mockups are attached as images. "
                      "Use these as visual reference for UI layout, component "
                      "structure, and user flow.",
          )
      )
  ```

**File: `agents/builtin/react_developer.py` — `execute()`**
- **Lines 67-117**
- Same pattern as application_architect — check for design_images artifact
- Add system message noting images are attached as visual reference for the Presentation sub-layer

**File: `agents/builtin/csharp_developer.py` — `execute()`**
- **Lines 69-119**
- Same pattern — only relevant when generating Blazor/MVC presentation code

**File: `agents/builtin/python_developer.py` — `execute()`**
- **Lines 67-117**
- Not typically relevant (Python backends don't generate UI), but include for Flask template generation

### Phase 5: Selective image injection by capability

Not all app stages need wireframes. A background worker stage shouldn't receive UI mockups.

**File: `stages/build_session.py` — `_decompose_app_stage()`**
- Only inject images for stages with `capability` in `("presentation", "domain")`:
  - `presentation` — always inject (this IS the UI)
  - `domain` — inject if the stage generates API endpoints (helps understand what the API serves)
  - `data-access`, `background` — never inject (no UI relevance)

### Phase 6: Graceful degradation

**File: `stages/build_session.py`**
- If the AI provider doesn't support vision (e.g., older model), fall back to text-only task
- Pattern from `discovery.py:1032-1051` — retry with text content if vision call fails
- Log warning: `"Vision not supported by AI provider — generating app code without wireframe reference"`

## Files to Modify

| File | Change |
|------|--------|
| `stages/discovery_state.py:384-431` | Persist images to disk, store metadata in state YAML |
| `stages/discovery.py:780` | Call `_persist_design_images()` after building vision content |
| `stages/build_session.py:165-200` | Load design images from discovery state in `__init__` |
| `stages/build_session.py:~1970` | Inject images into app-stage task via vision content array |
| `stages/build_session.py:3494-3534` | `_execute_with_retry()` — accept `task: str | list` |
| `stages/build_session.py:3540-3573` | `_execute_with_continuation()` — accept `task: str | list` |
| `agents/builtin/application_architect.py:72-123` | Check for design_images artifact |
| `agents/builtin/react_developer.py:67-117` | Check for design_images artifact |
| `agents/builtin/csharp_developer.py:69-119` | Check for design_images artifact |
| `agents/builtin/python_developer.py:67-117` | Check for design_images artifact (Flask templates) |

## Files to Create

| File | Purpose |
|------|---------|
| None | All changes are modifications to existing files |

## Variables & Parameters — Complete Chain

```
Discovery:
  binary_reader.read_file(path) → ReadResult
    .embedded_images: list[EmbeddedImage]
      .data: str (base64)
      .mime_type: str
      .source: str

  discovery.py._build_opening(images=artifact_images)
    artifact_images: list[dict] = [{"filename": str, "data": base64, "mime": str}]
    returns: list[dict] (OpenAI vision content array)

  discovery.py._persist_design_images(images) → NEW
    writes: .prototype/state/design_images/img_NNN.{ext}
    stores: discovery_state.design_images: list[{source, path, mime}]

Build:
  build_session.__init__()
    self._design_images: list[dict] = _load_design_images() → NEW
      reads: .prototype/state/design_images/*
      returns: [{path: str, mime: str, data: base64_str}]
    context.add_artifact("design_images", self._design_images)

  build_session._decompose_app_stage(stage, architecture, _print)
    checks: self._design_images and stage capability in ("presentation", "domain")
    constructs: task_content: list[dict] = [
      {"type": "text", "text": task_str},
      {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}},
      ...
    ]

  build_session._execute_with_retry(agent, task: str | list, ...)
    → _execute_with_continuation(agent, task: str | list, ...)
      → agent.execute(context, task)
        → context.ai_provider.chat(messages, ...)
          → messages_to_dicts(messages) — passes list content through
            → HTTP/SDK call with vision content array

Agent:
  base.py.execute(context, task: str | list)
    messages.append(AIMessage(role="user", content=task))
    # content: str | list already supported

  application_architect.py.execute(context, task)
    design_images = context.get_artifact("design_images")
    # If present, add system message noting images are attached

  react_developer.py.execute(context, task)
    # Same pattern — images as visual reference for Presentation sub-layer
```

## Token Budget Consideration

Each base64-encoded image consumes significant tokens:
- 1 PNG wireframe (~100KB) ≈ 1,000-2,000 vision tokens
- 10 wireframes ≈ 10,000-20,000 tokens

Cap at `MAX_DESIGN_IMAGES_PER_STAGE = 5` to prevent prompt bloat.
Prioritize by: (1) images from the same document section as this stage's services, (2) most recent uploads, (3) largest images (likely full-page wireframes vs icons).

## Testing

1. Unit: `_persist_design_images()` writes files and returns metadata
2. Unit: `_load_design_images()` reads persisted files correctly
3. Unit: vision content array construction matches OpenAI format
4. Unit: `_execute_with_retry()` accepts both str and list task
5. Integration: upload a PPTX with wireframes during discovery → build generates UI matching the wireframes
6. Graceful degradation: vision-unsupported provider falls back to text-only
