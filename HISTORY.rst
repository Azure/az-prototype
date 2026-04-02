.. :changelog:

Release History
===============

0.2.1b6
+++++++

Build resilience
~~~~~~~~~~~~~~~~~
* **Per-stage advisory with dedicated advisor agent** -- advisory notes
  are now generated per-stage immediately after QA passes, using a new
  ``advisor`` built-in agent.  Phase 4 aggregates per-stage advisories
  into ``ADVISORY.md`` with no AI call, eliminating the prompt-too-large
  error that occurred when all generated files exceeded the 168K token
  Copilot API limit.
* **``CopilotPromptTooLargeError``** -- new exception class raised when
  the Copilot API rejects a prompt for exceeding its token limit.
  Includes ``token_count`` and ``token_limit`` attributes for callers
  to decide how to truncate.  Design stage catches this and
  automatically trims the architecture context before retrying.
* **Copilot API error handling cleanup** -- removed the misleading
  "Ensure you have a valid GitHub Copilot Business or Enterprise license"
  message from all non-200 API errors (it was a red herring for token
  limit, timeout, and other failures).
* **Request ID logging** -- ``x-request-id`` response header from the
  Copilot API is now captured in the debug log for every request,
  enabling correlation with GitHub support.
* **Timeout retry with countdown** -- Copilot API timeouts trigger up
  to 5 retry attempts with escalating wait periods (15s, 30s, 60s, 120s).
  A live countdown timer shows seconds remaining before each retry,
  preventing the UI from appearing to hang.  Retry coverage includes
  generation, QA review, and remediation calls.
* **Rate limit handling (HTTP 429)** -- ``CopilotRateLimitError`` raised
  when the API returns 429.  The ``Retry-After`` header value is used for
  the countdown wait, falling back to the backoff schedule if missing.
  Rate limit events are logged with request ID for correlation.
* **Stage completion gating** -- stages are only marked ``"generated"``
  after passing QA.  New intermediate sub-states:

  - ``"generating"`` -- AI agent is producing files.  If interrupted
    (timeout, crash), re-entry deletes artifacts and regenerates.
  - ``"validating"`` -- files on disk, awaiting QA.  If QA fails after
    max remediation attempts, build stops.  User fixes files manually,
    re-runs build, and QA re-validates without regenerating.
  - ``"generated"`` -- QA passed.  Terminal success state.

* **Downstream cascade on re-validation** -- when a ``"validating"``
  stage passes QA on re-run (user fixed it), all downstream
  ``"generated"`` stages are reset to ``"pending"`` so they regenerate
  with updated upstream outputs.
* **QA failure output cleanup** -- when QA fails and stops the build,
  only issue descriptions and fix instructions are shown.  Full file
  contents are no longer printed to the console.
* **Application code stages** -- Phase 1 prompt now instructs the
  architect to create ``category: "app"`` stages for source code,
  with explicit dependency chain documentation ensuring app stages
  come after all infrastructure stages.
* **``CopilotTimeoutError``** -- new exception class (extends
  ``CLIError``) enables retry logic to catch timeouts specifically
  without catching other API errors.

Benchmark suite
~~~~~~~~~~~~~~~~
* **14-benchmark quality suite** -- project-agnostic benchmarks (B-INST
  through B-ANTI) measuring instruction adherence, constraint compliance,
  technical correctness, security posture, operational readiness, dependency
  hygiene, scope discipline, code quality, output completeness, cross-stage
  consistency, documentation quality, response reliability, RBAC
  architecture, and anti-pattern absence.  Each benchmark scored 0-100 with
  4-5 weighted sub-factors.
* **Benchmark report template** (``benchmarks/TEMPLATE.html``) -- reusable
  HTML template with fixed rendering engine; only data arrays change between
  runs.  Includes per-stage dimension tables, analysis notes, systematic
  strengths/weaknesses, critical bugs table, and dimension heatmap.
* **Benchmark trends dashboard** (``benchmarks/overall.html``) -- Chart.js
  time-series dashboard with per-benchmark detail tabs showing sub-factor
  breakdowns, scoring methodology, and improvement areas with severity.
* **PDF report generation** (``scripts/generate_pdf.py``) -- populates
  ``benchmarks/TEMPLATE.docx`` with scores, generates matplotlib charts
  (overall trend, 14 factor comparisons, 14 score trends), embeds all 29
  charts into the DOCX, converts to PDF via ``docx2pdf``, and cleans up
  the temporary DOCX.
* **Scoring instructions** (``benchmarks/INSTRUCTIONS.md``) -- testing
  methodology, extraction scripts, copy-paste analysis instructions, and
  report generation rules.

Build quality improvements
~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Tag placement root cause fix** -- constraint on line 36 of
  ``terraform_agent.py`` said "in body block", directly causing tags-inside-
  body across 11/14 stages.  Changed to "CRITICAL: as top-level attribute".
  Added dedicated ``## CRITICAL: TAGS PLACEMENT`` section with correct and
  incorrect examples.
* **NEVER directive hierarchy** -- added ``## CRITICAL: DIRECTIVE HIERARCHY``
  section to ``build_session.py``.  NEVER/MUST directives in policies now
  explicitly override architecture context and POC notes during generation.
  Users can still override post-generation via PolicyResolver.
* **deploy.sh requirements** -- replaced bullet list in ``TERRAFORM_PROMPT``
  with 13-point ``## CRITICAL: deploy.sh REQUIREMENTS`` section.  Scripts
  under 100 lines are rejected.  Must include ``--dry-run``, ``--destroy``,
  ``--help``, pre-flight validation, and post-deployment verification.
* **Scope boundary enforcement** -- added ``## CRITICAL: SCOPE BOUNDARY``
  section.  Resources not listed in "Services in This Stage" and not
  required by policy companions are rejected.
* **Provider hygiene** -- added ``## CRITICAL: PROVIDER RESTRICTIONS``
  section.  Only ``hashicorp/azapi`` allowed; ``azurerm`` and ``random``
  providers rejected.  Corresponding QA checklist updated.
* **Subnet drift prevention** -- VNET-001 policy rewritten for both
  Terraform and Bicep: VNet declares only ``addressSpace``; subnets are
  separate child resources.  New prohibition: "NEVER define subnets inline
  in the VNet body."  Added ``## CRITICAL: SUBNET RESOURCES`` to
  ``TERRAFORM_PROMPT``.
* **Networking stage boundary** -- expanded ``_get_networking_stage_note()``
  to explicitly prohibit PE/DNS creation in service stages when a networking
  stage handles them.
* **Application code stages** -- Phase 1 deployment plan prompt now
  instructs the architect to create ``category: "app"`` stages for
  application source code (APIs, workers, functions, web apps, Logic Apps)
  separate from ``category: "infra"`` stages that provision Azure
  resources.  Stage ordering now documents full dependency chain: each
  group (identity, monitoring, networking, data, compute, integration,
  app, docs) lists what it depends on and what it provides downstream.
* **Empty message filtering** -- ``CopilotProvider._messages_to_dicts()``
  now skips messages with empty, None, or whitespace-only content to
  prevent HTTP 400 errors.  ``BaseAgent.get_system_messages()`` adds
  ``.strip()`` guards on governance, standards, and knowledge text.
  Root cause was ``set_governor_brief(" ")`` (single space) which
  created a whitespace-only system message rejected by the API.
* **RBAC principal separation** -- added Section 6.4 to ``constraints.md``:
  administrative roles target deploying user, data roles target app MI.
* **Cosmos DB RBAC documentation** -- added Section 6.5 to
  ``constraints.md``: data-plane roles must use ``sqlRoleAssignments``, not
  ARM ``roleAssignments``.
* **azapi v2.x semantics** -- provider version injection now documents v2.x
  semantics (top-level tags, ``.output.properties`` access, native HCL
  body maps).
* **Documentation agent max_tokens** -- increased from 4,096 to 204,800
  (approx 350-400 pages) to prevent Stage 14 truncation.
* **Documentation agent prompt** -- enriched with context handling,
  completeness requirement, and explicit instructions to reference actual
  stage outputs.
* **Upstream dependency enforcement** -- task prompt and
  ``TERRAFORM_PROMPT`` now explicitly state "CRITICAL: Only add
  terraform_remote_state blocks for stages listed as upstream
  dependencies."  Prevents unnecessary dependencies (e.g., Stage 10
  referencing Stage 4 networking when it has no networking dependency).

Governance restructuring
~~~~~~~~~~~~~~~~~~~~~~~~~
* **Domain-prefixed policy IDs** -- all 425 policy rule IDs renamed with
  domain prefixes for clarity:

  - ``AZ-`` for Azure service-specific rules (321 rules)
  - ``WAF-COST-`` for cost optimization (20 rules)
  - ``WAF-PERF-`` for performance (25 rules)
  - ``WAF-REL-`` for reliability (20 rules)
  - ``WAF-SEC-`` for security principles (16 rules)
  - ``CC-INT-`` for cross-cutting integration patterns (26 rules)

* **Well-Architected Framework alignment** -- cost, performance,
  reliability, and security policies organized under WAF categories.
  Integration patterns separated as cross-cutting.
* **STAN- prefixed standard IDs** -- all 38 design standard principle IDs
  renamed with ``STAN-`` prefix for consistency:

  - ``STAN-DES-`` for design principles (5 principles)
  - ``STAN-CODE-`` for coding standards (5 principles)
  - ``STAN-PY-`` for Python application standards (5 principles)
  - ``STAN-CS-`` for .NET application standards (5 principles)
  - ``STAN-BCP-`` for Bicep module standards (8 principles)
  - ``STAN-TF-`` for Terraform module standards (10 principles)

* **Wiki governance subpages** -- 17 dedicated wiki pages with per-service
  policy tables (rule ID, description, agents), auto-generated from YAML
  via ``scripts/generate_wiki_governance.py``.

Anti-pattern detection
~~~~~~~~~~~~~~~~~~~~~~~
* **ANTI- prefixed IDs** -- all anti-pattern checks now have explicit IDs
  with ``ANTI-`` prefix:

  - ``ANTI-SEC-`` for security (6 checks)
  - ``ANTI-AUTH-`` for authentication (3 checks)
  - ``ANTI-NET-`` for networking (5 checks)
  - ``ANTI-STOR-`` for storage (2 checks)
  - ``ANTI-CONT-`` for containers (2 checks)
  - ``ANTI-ENC-`` for encryption (3 checks)
  - ``ANTI-MON-`` for monitoring (2 checks)
  - ``ANTI-COST-`` for cost (3 checks)
  - ``ANTI-COMP-`` for completeness (8 checks)
  - ``ANTI-TFS-`` for Terraform structure (7 checks)
  - ``ANTI-BCS-`` for Bicep structure (7 checks)

  Scanner output now includes the check ID in each warning
  (e.g., ``[ANTI-SEC-001] Possible credential/secret...``).
* **New domain: ``bicep_structure``** -- 7 new anti-pattern checks for
  inline resources, listKeys/listSas usage, hardcoded names, missing
  @description decorators, missing outputs, deploy.sh error handling,
  and outdated API versions.
* **New domain: ``terraform_structure``** -- 7 anti-pattern checks for
  unused azurerm/random providers, azapi v1.x versions, non-deterministic
  ``uuid()``, ``jsondecode()`` on v2.x output, and azurerm resource usage.
  Total checks: 48 across 11 domains.
* **Hardcoded upstream name detection** -- new completeness check catches
  ALZ-patterned hardcoded resource names (``zd-``, ``pi-``, ``pm-``,
  ``pc-`` prefixes).
* **QA scope compliance** -- added Section 8 to QA engineer checklist:
  scope compliance, tag placement, and azurerm resource checks.
* **Anti-pattern scan skips documentation stages** -- docs describe the
  architecture (including SQL auth, public access patterns) which triggered
  false positives.  Scan now skips stages with ``category == "docs"``.
* **ANTI-NET-006/007** -- new checks for invalid placeholder private endpoints
  pointing at VNets (ARM 400 at deploy time) and VNet/NSG diagnostic settings
  using ``allLogs`` category (only ``AllMetrics`` is supported).
* **Networking stage guidance** -- ``TERRAFORM_PROMPT`` and ``BICEP_PROMPT``
  now include ``## CRITICAL: NETWORKING STAGE RULES`` preventing placeholder
  PEs and wrong diagnostic categories.  QA checklist updated with Section 9
  (Networking Stage) and anti-oscillation guidance.
* **Safe pattern audit** -- tightened overly broad safe patterns across all
  anti-pattern domains.  Removed ``"production"``, ``"development"``,
  ``"identity"``, ``"least privilege"`` and other single-word patterns that
  caused cross-contamination at the whole-text scan level.
* **ANTI-NET-008** -- detect diagnostic settings on NSG resources (NSGs have
  no log or metric categories; ARM rejects with HTTP 400).
* **ANTI-MON-003** -- detect deprecated ``InstrumentationKey`` outputs (use
  ``connection_string`` instead).
* **Private DNS zone lookup** (``knowledge/private_dns_zones.py``) -- static
  mapping of ARM resource types to exact private DNS zone FQDNs, injected
  into the networking stage task prompt.  Eliminates DNS zone naming errors.
* **Extension resource tag guidance** -- terraform and bicep agent prompts now
  explicitly prohibit ``tags`` on ``diagnosticSettings``, ``roleAssignments``,
  and ``locks`` (ARM extension resources that reject tags with HTTP 400).
* **deploy.sh correctness rules** -- terraform agent prompt now documents that
  ``terraform output`` has no ``-state=`` flag, and cleanup traps must use
  captured ``$?`` not script-level variables.
* **Service registry normalization** -- renamed ``bicep_resource`` to
  ``resource_type`` and ``bicep_api_version`` to ``api_version`` across all
  30 service entries.  Removed ``terraform_resource`` (listed ``azurerm_*``
  names which are wrong for azapi).  Added Cosmos DB child resources
  (``sqlRoleAssignments``, ``sqlDatabases``, ``sqlContainers``).
* **Knowledge file azapi migration** -- converted 424 ``azurerm_`` references
  across 25 service knowledge files to ``azapi_resource`` patterns.  This was
  a major source of incorrect resource patterns in generated code.
* **Container Apps identity rules** -- added to ``container-apps.md``:
  UAMI required for ACR pull, no circular ``depends_on``,
  ``AZURE_CLIENT_ID`` for multi-identity disambiguation.
* **New anti-patterns**: ANTI-CONT-003 (SystemAssigned-only with ACR),
  ANTI-AUTH-004 (Key Vault missing Crypto User), ANTI-COMP-009 (Storage
  Blob Delegator vs Data Contributor).
* **QA false positive fix** -- Section 10 (Output Consistency) no longer
  flags cross-stage output keys as "non-standard" when they match the
  actual exported names from upstream stages.
* **IaC tool scoping** -- anti-pattern checks now support ``applies_to``
  field (domain-level or pattern-level, never both in the same file).
  Bicep-structure checks only run on Bicep builds, Terraform-structure
  and TF-specific completeness checks only on Terraform.  Generic domains
  (security, networking, etc.) run on all builds.  ``scan()`` accepts
  optional ``iac_tool`` parameter.
* **``az prototype validate``** -- new CLI command to validate all
  governance files (policies, anti-patterns, standards, workloads).
  Flags: ``--all``, ``--policies``, ``--anti-patterns``, ``--standards``,
  ``--workloads``, ``--strict``.  CI pipelines consolidated to a single
  validation step.

DRY refactoring
~~~~~~~~~~~~~~~~~
* **``BaseState`` class** -- extracted shared ``__init__``, ``load()``,
  ``save()``, ``_deep_merge()``, ``exists``/``state`` properties into
  ``stages/base_state.py``.  All 4 state managers (build, deploy,
  discovery, backlog) inherit from it.  Post-load hooks via
  ``_post_load()`` for migrations and backfills.
* **``_apply_governance_check()``** -- extracted the duplicated 12-line
  governance warning block from 6 agent ``execute()`` overrides into a
  single method on ``BaseAgent``.  Each agent now calls
  ``return self._apply_governance_check(response, context)``.
* **AI provider shared utilities** -- moved ``_messages_to_dicts()`` and
  ``_extract_tool_calls()`` from 3 provider files into ``ai/provider.py``
  as ``messages_to_dicts()`` and ``extract_tool_calls_from_openai()``.
  Copilot provider uses ``filter_empty=True`` for its specific need.
* **``SessionMixin``** -- extracted shared ``_maybe_spinner()``,
  ``_countdown()``, ``_setup_token_tracker()``, and
  ``_setup_escalation_tracker()`` into ``stages/session_mixin.py``.
  All 4 session classes (build, deploy, discovery, backlog) inherit it.
* **``safe_load_yaml()``** -- shared YAML loading helper in
  ``governance/__init__.py`` replaces duplicated try/except blocks
  in anti-patterns and standards loaders.

Prompt optimization (58 fixes)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **TERRAFORM_PROMPT rewrite** -- complete rewrite with CRITICAL sections for
  ``response_export_values``, state file naming convention, cross-stage
  dependencies via ``terraform_remote_state``, output naming convention,
  deploy.sh standardization (logging functions, control flow, auto-approve
  pattern, env var convention), design notes format, diagnostic settings
  pattern, provider block template, and variable validation examples.
* **BICEP_PROMPT parity** -- added file structure rules, deploy.sh
  requirements (150-line minimum with argument parsing), subnet drift
  prevention, diagnostic settings, design notes format, and output format
  rules matching the Terraform prompt.
* **APP_DEVELOPER_PROMPT enrichment** -- added Azure service connection
  patterns (Cosmos DB, Storage, Key Vault, Service Bus with
  DefaultAzureCredential), deploy.sh requirements (container build/push,
  health check, rollback), and project structure template.
* **Prior stage output key injection** -- downstream stages now see exact
  output key names from previously generated stages, eliminating output
  name mismatches.
* **RBAC enforcement language** -- companion requirements now explicitly
  require ALL listed roles in the current stage with no deferral.
* **Policy agent name fix** -- ``_resolve_service_policies()`` uses actual
  IaC tool agent name instead of hardcoded ``"terraform-agent"``.
* **Advisory notes to file** -- advisory review output saved to
  ``concept/docs/ADVISORY.md`` instead of printing to screen.
* **QA checklist expansion** -- added checks for ``response_export_values``,
  empty files, ``required_version``, state file naming, output key naming
  consistency, and remote state path matching.
* **Anti-pattern expansion** -- 40 checks across 10 domains (was 39).
  Added ``.output.properties`` without ``response_export_values`` detection
  and data-plane role name safe_patterns for spurious warning prevention.
* **Documentation agent** -- added exact directory path guidance, actual
  SKU value guidance, and mandatory deployment guide section list.
* **deploy.sh single-dash ``-auto-approve``** -- Terraform uses single
  dash ``-auto-approve`` not double dash.  Fixed in the deploy.sh
  template.
* **Storage container API version** -- added ``blobServices/containers``
  child resource entry to service registry with verified ``@2023-05-01``
  version.  ``resource_metadata.py`` now checks parent service
  ``child_resources`` entries before falling through to Microsoft Learn
  runtime lookup.

Truncation recovery
~~~~~~~~~~~~~~~~~~~~
* **Continuation now carries conversation history** --
  ``_execute_with_continuation()`` appends the truncated response as an
  assistant message to ``conversation_history`` before requesting a
  continuation.  Previously the model had no context of what it already
  generated, causing it to respond with "I don't have previous context"
  instead of continuing where it left off.
* **Documentation path fix** -- removed ``docs/`` prefix from code block
  labels in ``DOCUMENTATION_PROMPT``.  The stage directory already
  provides ``concept/docs/``, so labeling files as ``docs/architecture.md``
  produced a nested ``concept/docs/docs/architecture.md`` path.
* **Documentation stage context enrichment** -- new
  ``_build_docs_context()`` reads actual ``outputs.tf`` from each
  previously generated stage and injects output names, descriptions, and
  file lists into the documentation prompt.  This ensures docs reflect
  real build artifacts (including QA remediation changes) rather than
  just the planned architecture.

AI provider
~~~~~~~~~~~~
* **Copilot default timeout** increased from 480s to 600s (10 minutes)
  to accommodate large QA remediation prompts (200KB+).

Discovery session
~~~~~~~~~~~~~~~~~~~
* **Unified discovery tracking (``TrackedItem``)** — consolidated three
  independent tracking systems (``topics``, ``open_items``,
  ``confirmed_items``) into a single ``items`` list of ``TrackedItem``
  objects.  Each item carries a ``kind`` (``"topic"`` or ``"decision"``)
  and a ``status`` (``"pending"``, ``"answered"``, ``"confirmed"``,
  ``"skipped"``).  Legacy state files are automatically migrated on load.
* **Immutable discovery topics across re-runs** — topics are established
  once, persisted to ``discovery.yaml``, and immutable.  Re-running
  ``az prototype design`` resumes at the first unanswered topic.  New
  artifacts can only *add* topics, never replace existing ones.
* **Artifact inventory with content hashing** — ``discovery.yaml`` tracks
  SHA-256 hashes for every artifact file and the ``--context`` string.
  Re-runs only read/analyze new or changed files; unchanged content is
  skipped entirely.
* **``--context`` records decisions and exits cleanly** — simple directives
  (e.g. "change app name to X") are recorded as confirmed decisions and
  the session exits immediately.  Decision items (``kind="decision"``) are
  no longer walked interactively — only ``kind="topic"`` items require input.
* **``--reset`` now clears discovery state** — clears topics, conversation
  history, artifact inventory, and all structured fields.
* **``###`` subsections folded into parent topics** — only ``##`` (level-2)
  headings become discovery topics.  Level-3 subsections are included in
  their parent topic's content.  The biz-analyst prompt explicitly prohibits
  ``###`` headings.

Slash commands
~~~~~~~~~~~~~~~
* **Slash commands no longer consume topic iterations** — the inner
  follow-up loop (max 5 per topic) only counts real AI exchanges.
  Slash commands and empty inputs do not advance the counter.
* **Improved ``/why`` output** — snippets increased from 150 to 500 chars;
  each exchange shows which discovery topic was being discussed.
* **``/restart`` breaks out of section loop** — previously reset state but
  left the session iterating stale topics.

Governor agent
~~~~~~~~~~~~~~~
* **Governor agent — embedding-based policy enforcement** — new built-in
  agent (``governor``) that replaces injecting all 13 policy files (~40KB)
  into every agent's system prompt.  Three modes: ``brief()`` retrieves the
  top-K most relevant rules (~1-2KB); ``review()`` evaluates output against
  the full policy set using parallel chunked AI calls (``max_workers=2``).
  **Wired into the build session** — before each stage's agent generates
  code, the governor produces a policy brief for the specific stage context
  (e.g. "generate terraform for Foundation: managed-identity, log-analytics")
  and injects it via ``set_governor_brief()``.  This ensures generated code
  is policy-compliant from the start rather than relying solely on post-
  generation QA to catch violations.  The brief is also injected directly
  into the task prompt as a ``## MANDATORY GOVERNANCE RULES`` section near
  the end (where models pay the most attention), not just in system messages
  where it was drowned out in 600KB+ prompts.  Policy requirements like private endpoints and network isolation are
  enforced through governance policies, not hardcoded as agent constraints.
* **One-time architecture condensation** — the full architecture document
  (542KB in real projects) is condensed into per-stage context summaries
  (~1KB each) via a single AI call after plan derivation.  Each generation
  call then uses only the condensed context + governor brief + task
  instructions (~14KB total instead of 622KB).  The governance brief is
  now ~11% of the prompt (vs 0.24% previously), a 46x visibility increase.
  Knowledge docs and standards are stripped from generation calls to keep
  prompts focused.
* **Governance-reinforced QA remediation** — max attempts increased to 3
  with escalating severity.  Each remediation attempt uses condensed context
  and re-applies the governor brief.  Severity escalates from "MUST fix"
  to "CRITICAL" to "FINAL ATTEMPT — build will be rejected permanently."
* **Governor brief includes rationale and anti-patterns** — MUST rules
  include implementation rationale.  ALL anti-pattern violation patterns
  (33 checks across 9 domains) are appended as ``NEVER GENERATE``
  directives, loaded directly from governance YAML files with zero
  hardcoded logic.
* **NET-001 updated** — now explicitly requires disabling public network
  access AND using private endpoints (previously only mentioned endpoints).
* **NET-005 added** — requires ``publicNetworkAccess = Disabled`` in both
  Terraform and Bicep.  Covers the gap where Azure defaults to Enabled.
* **Anti-pattern safe_patterns** — networking anti-pattern now exempts
  ``public_network_access_enabled = false`` and
  ``publicnetworkaccess = "disabled"`` to avoid false positives.
  Agents receive focused policy briefs via ``set_governor_brief()``.
* **Pre-computed neural embeddings** — built-in policy embeddings are
  generated at build time (``scripts/compute_embeddings.py``) using
  ``sentence-transformers`` (``all-MiniLM-L6-v2``) and shipped inside the
  wheel as ``policy_vectors.json``.  No ``torch`` needed at runtime — works
  on all platforms including Azure CLI's 32-bit Windows Python.  Custom
  policies use TF-IDF; non-Windows users can install
  ``sentence-transformers`` for neural custom-policy embeddings.
* **New ``AgentCapability.GOVERNANCE``** enum value.  Built-in agent count:
  11 → 12.

AI provider & telemetry
~~~~~~~~~~~~~~~~~~~~~~~~
* **Copilot default timeout** increased from 300s to 480s.
* **Lightweight AI call for ``--context`` re-entry** — topic classification
  uses a ~0.5KB prompt instead of the full ~69KB governance/template stack.
* **PRU tracking for Copilot users** — status bar shows Premium Request
  Units computed locally from the official multiplier table (e.g. Claude
  Sonnet 4 = 1, Haiku 4.5 = 0.33, Opus 4.5 = 3).  Non-Copilot providers
  are unaffected.

TUI & UX
~~~~~~~~~
* **Build and deploy now launch TUI** — ``az prototype build`` and
  ``az prototype deploy`` route through the TUI (``PrototypeApp``) for
  interactive sessions, matching the design stage.  Dry-run, ``--json``,
  single-stage deploy, and non-interactive contexts use the legacy path.
  Stage kwargs (``--reset``, ``--scope``, etc.) are passed through to
  the stage execution.
* **Downstream stages marked pending on re-run** — re-running an earlier
  stage (e.g. design after build+deploy) now marks all downstream stages
  as pending in the TUI tree, reflecting that they depend on the changed
  output and will need to be re-run.
* **Token status auto-updates after every AI call** — ``TokenTracker`` now
  fires an ``_on_update`` callback after each ``record()`` call.  All four
  sessions (discovery, build, deploy, backlog) wire this to the console or
  TUI adapter so the bottom-right status bar updates continuously.
  During AI calls, a live elapsed timer ticks in the status bar
  (e.g. "Analyzing architecture... (5s)") so the user knows the
  extension is working, then switches to token counts after the
  response arrives.
* **TUI quit shortcut** changed from Ctrl+C to Ctrl+Q.
* **Discovery UX** — clear call-to-action after AI response; trailing
  colons stripped from topic headings in the stage tree.

Debug logging
~~~~~~~~~~~~~~
* **Exhaustive debug logging (``DEBUG_PROTOTYPE=true``)** — creates a
  timestamped ``debug_YYYYMMDDHHMMSS.log`` in the project directory.
  Logs full AI call payloads (system message sizes, user content, response
  content, token counts, timing), every state mutation, every decision
  branch, every slash command, and full error tracebacks.

Governance policies (comprehensive overhaul)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **90 governance policies, 428 rules** — up from 13 policies / 65 rules.
  Every policy now includes exact ``terraform_pattern`` (azapi) and
  ``bicep_pattern`` code, ``companion_resources`` with full IaC code,
  and ``prohibitions`` listing what agents must never generate.
* **67 Azure service policies** across 11 subcategories: ai (5), compute (6),
  data (16), identity (2), management (4), messaging (2), monitoring (3),
  networking (17), security (4), storage (1), web (7).  Updated with
  WAF service guide recommendations (40 new rules across 10 services).
* **4 security policies** rewritten: authentication (5 rules), data
  protection (7 rules), managed identity (6 rules), network isolation
  (8 rules).  Aligned with Azure Well-Architected Framework Security
  pillar (SE-01 through SE-05).
* **6 integration policies**: APIM↔Container Apps (rewritten, 6 rules),
  event-driven (5 rules), data-pipeline (4 rules), microservices
  (5 rules), api-patterns (4 rules), frontend-backend (4 rules).
* **4 cost policies** (new): SKU selection, scaling, resource lifecycle,
  reserved instances.  Aligned with WAF Cost Optimization pillar.
* **5 performance policies** (new): caching, database optimization,
  compute optimization, networking optimization, monitoring/observability.
  Aligned with WAF Performance Efficiency pillar.
* **4 reliability policies** (new): high availability, backup/recovery,
  fault tolerance, deployment safety.  Aligned with WAF Reliability
  pillar (RE-01 through RE-05).
* **Exact service matching with relevance filtering** —
  ``PolicyEngine.resolve_for_stage()`` uses exact service name matching
  (not embedding similarity).  Cross-cutting policies (3+ services) are
  only included when at least half their services overlap with the stage,
  preventing prompt bloat from irrelevant patterns.  IaC-tool filtering
  strips Bicep patterns for Terraform builds and vice versa.
* **Deterministic prompt injection** — ``_resolve_service_policies()``
  injects matched policies into both generation and QA prompts as
  ``## MANDATORY RESOURCE POLICIES`` with exact code templates.

Build session
~~~~~~~~~~~~~~
* **Two-phase deployment plan derivation** — Phase 1: the architect
  produces a simple map of stages and services (no SKUs, no naming,
  no governance needed).  Phase 2: given the map, the architect fills
  in computed names, resource types, and SKUs with ALL relevant
  governance policies injected (since the service list is now known).
  Eliminates SKU conflicts (e.g. Basic ACR when policy requires
  Premium).
* **Service policies injected early in prompt** — ``MANDATORY RESOURCE
  POLICIES`` section moved from position 13 (near end) to position 3
  (right after services list).  Ensures the AI reads the exact code
  templates with correct property values before it starts generating.
* **Enforce ``concept/`` output directory** — ``_normalise_stages()``
  detects when the AI uses the project name as root and fixes it.
* **``--reset`` cleans non-concept output dirs** — loads build state
  before reset to find and clean project-named directories.
* **Pre-fetched API versions per resource type** — resolves correct
  API version from service-registry.yaml (fast) or Microsoft Learn
  (fallback) before generation.
* **Companion resource requirements** — RBAC roles, role GUIDs, and
  auth method injected per-service from the service registry.
* **Truncation recovery** — ``_execute_with_continuation()`` detects
  ``finish_reason == "length"`` and auto-continues (4 call sites).
* **``_max_tokens`` raised to 102,400** — Terraform, Bicep, App
  Developer, and QA agents.
* **QA reviews full file content** — no per-file or total size caps.
* **Mandatory stage ordering** — Foundation=1, Networking=2 enforced
  in architect prompt and ``_ensure_private_endpoint_stage()``.
* **Networking stage auto-injection** — when services need private
  endpoints, a networking stage with VNet + all PEs is injected at
  position 2 after Foundation.
* **Full QA report on max remediation** — when QA exhausts all
  remediation attempts, the full remaining issues report is printed
  (was truncated to 200 chars).
* **Full diagnostic logging** — task prompts log the FULL prompt
  sent to the AI (``task_full``), the FULL response (``content_full``),
  resolved service policies (``policy_full``), anti-pattern violations
  detected before the policy resolver, ``max_tokens`` sent per request,
  and ``finish_reason`` on every response.
* **TUI Ctrl+Q cancellation** — ``print_fn``, ``response_fn``, and
  ``status_fn`` now raise ``ShutdownRequested`` when the shutdown flag
  is set, breaking the worker thread out of QA/remediation loops
  immediately after the current HTTP call completes.  Previously the
  TUI exited but the prompt hung until the full QA loop finished.

QA agent
~~~~~~~~~
* **QA receives service policies + API versions** — same deterministic
  briefs injected into generation are also sent to QA for verification.
* **Provider compliance** (Terraform only) — no ``azurerm_*`` resources.
  Scoped to Terraform builds only — Bicep builds never see azurerm
  constraints.
* **Three-tier issue detection** — ``VERDICT: PASS/FAIL`` (handles
  markdown bold), pass phrases, keyword fallback.  Eliminates false
  positives from QA responses containing "critical" in headings.
* **VERDICT instruction in QA prompt** — QA must end every review
  with ``VERDICT: PASS`` or ``VERDICT: FAIL``.  WARNING-only results
  use PASS.  Without this, QA never emitted verdicts and the keyword
  fallback caused unnecessary remediation cycles.
* **Checklist items 8 + 9** — Provider Compliance and API Version
  Compliance added to Mandatory Review Checklist.
* **Credential false positive fix** — ``connectionstring`` safe patterns
  now include ARM property references and instrumentation context.

Terraform agent
~~~~~~~~~~~~~~~~
* **RBAC role assignment names** — ``random_uuid`` resource from
  ``hashicorp/random`` provider.  ``guid()`` does not exist in HCL.
* **publicNetworkAccess** — "ALWAYS set Disabled, networking stage
  handles private endpoints."

Security reviewer agent
~~~~~~~~~~~~~~~~~~~~~~~~
* **Public endpoints are blockers** — unless the user explicitly
  overrides, public endpoints and missing VNET are now BLOCKERs in
  all environments (was WARNINGs for POC).

Knowledge base
~~~~~~~~~~~~~~~
* **Eliminated public access contradictions** — removed all "POC
  relaxation" language from ``constraints.md``, service knowledge
  files, and agent prompts that told the AI public endpoints were
  acceptable for POC.  Private endpoints and VNET integration are
  now the default for all environments unless the user explicitly
  overrides via discovery directives or custom policies.
* **Fixed 9 service knowledge files** — ``public_network_access_enabled``
  changed from ``true`` to ``false`` in Terraform examples; Bicep
  examples changed from ``'Enabled'`` to ``'Disabled'``; POC Defaults
  tables changed from "Enabled (POC)" to "Disabled (unless user
  overrides)".
* **Copilot model catalogue** — added ``claude-sonnet-4-6`` to the
  fallback model list.

Build & CI/CD
~~~~~~~~~~~~~~
* Build scripts (``build.sh``, ``build.bat``) and all CI/CD workflows
  (``ci.yml``, ``pr.yml``, ``release.yml``) compute policy embeddings
  before wheel construction.
* New policy subdirectories (azure/*, cost, performance, reliability)
  have ``__init__.py`` files for proper package discovery.
* Renamed ``--script-resource-group`` deploy flag to ``--script-rg``.

Cleanup
~~~~~~~~
* Removed vestigial ``_SECTION_COMPLETE_MARKER`` (defined but never used).
* Removed dead code: ``build_incremental_update_prompt()``, ``items_by_kind()``.

0.2.1b5
+++++++

Backlog enrichment
~~~~~~~~~~~~~~~~~~~
* **Enriched backlog with full project context** — ``generate backlog``
  now loads build stages, deploy status, cost analysis, and stage
  completion (same context as spec-kit) for richer item generation.
* **Completed work items** — items for already-built/deployed stages are
  generated with ``status: done`` and grouped under a "Completed POC Work"
  epic, with tasks marked as done.
* **Production Readiness epic** — dedicated epic for POC-to-production
  work (SKU upgrades, networking, CI/CD, monitoring, DR), separate from
  generic "Deferred / Future Work".
* **Azure DevOps hierarchy** — generation prompt requests Feature → User
  Story → Task structure with ``children[]``; push code now creates Task
  work items linked to their parent User Story.
* **Dict task format** — tasks can be ``{"title": "...", "done": true}``
  objects; GitHub issues render completed tasks as ``[x]``, DevOps
  descriptions show checkbox markers.  String tasks remain supported.

Spec-kit enrichment
~~~~~~~~~~~~~~~~~~~~
* **Enriched spec-kit with full project context** — ``generate speckit``
  now loads discovery state, build stages, deploy status, cost analysis,
  and stage completion to populate templates with real project data.
* **Per-template prompt overrides** — each spec-kit template gets a
  tailored AI prompt that tells the doc-agent exactly which context
  sections to use and what output format to produce.
* **production.md** — new template covering POC-to-production guidance:
  SKU upgrades, networking, CI/CD, monitoring, DR, load testing, and
  estimated production costs.
* **Restructured tasks.md** — tasks now map 1:1 to build/deploy stages
  with status markers: ``[x]`` completed, ``[!]`` failed, ``[ ]`` pending.
  Added Phase 6 (Production Readiness) for hardening tasks.

Init improvements
~~~~~~~~~~~~~~~~~~
* **Removed eager directory creation from init** — ``concept/apps/``,
  ``concept/infra/`` (terraform/, bicep/), and ``concept/db/`` (sql/,
  cosmos/, databricks/, fabric/) are no longer created during
  ``az prototype init``.  These directories are now created on demand
  by the build stage only when they are actually needed.
* **Fixed --output-dir nesting** — ``--output-dir ./my-output`` now
  uses the specified directory as the project root instead of creating
  a ``name/`` subdirectory inside it.
* **Fixed "Next: cd ..." hint** — the summary panel now shows the
  actual project directory name instead of always displaying the
  ``--name`` value.
* **--json flag on all commands** — added ``json_output`` parameter to
  every command function so the global ``--json`` / ``-j`` flag is
  accepted on all 24 commands (previously only 3 accepted it).
* **Naming env/zone_id derived from --environment** — ``naming.env``
  and ``naming.zone_id`` in ``prototype.yaml`` now reflect the chosen
  environment (dev→dev/zd, staging→stg/zs, prod→prd/zp) instead of
  always defaulting to ``dev``/``zd``.

TUI stage tree fix
~~~~~~~~~~~~~~~~~~
* **Fixed stage tree showing completed checkmark for unstarted stages** —
  when launching with ``--stage design`` from an init-only project, the
  Design stage now correctly shows as in-progress (●) instead of
  completed (✓).  Stage status is now derived from detected state files,
  not the target stage.
* **Stage skip guard** — ``--stage deploy`` from an init-only project
  now prints a warning and falls back to the next valid stage (e.g.
  design) instead of allowing users to skip ahead.
* **Consistent "no project" error message** — all commands now show the
  same red ``CLIError`` message when ``prototype.yaml`` is missing:
  *"No prototype project found. Run 'az prototype init'."*
* **Replaced ``--output-format`` with ``--table`` / ``--report``** — the
  ``analyze costs`` command shows the cost summary table by default,
  ``--table`` shows the summary without saving a file, ``--report``
  shows the full detailed report, and ``--json`` returns raw JSON.
  The ``generate backlog`` command uses ``--table`` instead of
  ``--output-format``.
* **``--json`` on cost analysis returns full content** — ``--json`` now
  suppresses console output and returns a structured JSON dict with the
  full cost report in the ``content`` field for machine consumption.
* **``generate docs`` default output moved to ``concept/docs/``** —
  documentation is now generated alongside other concept artifacts
  instead of a separate ``docs/`` directory at the project root.
* **Normalized path separators in generate output** — displayed paths
  now use forward slashes on all platforms instead of mixed separators
  on Windows.  Also handles cross-mount ``--path`` values (e.g.
  ``Y:\output`` from a ``\\Mac\projects`` project) without crashing.
* **Spec-kit generates its own templates** — ``generate speckit`` now
  produces spec-kit-specific files (``constitution.md``, ``spec.md``,
  ``plan.md``, ``tasks.md``) aligned with the `spec-kit
  <https://github.com/github/spec-kit>`_ format instead of duplicating
  the documentation templates.

TUI post-design improvements
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Removed CLI "Next steps" from TUI** — the ``az prototype`` CLI
  commands block is no longer printed into the TUI console after
  architecture generation; replaced with a continuation prompt.
* **"continue" launches build** — typing ``continue`` after design
  completes now starts the build stage, matching the prompt text.
* **Reduced console noise** — removed extra blank lines between
  "Planning...", "Generating architecture...", and feasibility
  check messages in the TUI output.
* **Feasibility wording** — changed "Reviewing {iac} feasibility..."
  to "Confirming {iac} feasibility..." and removed the arrow prefix.

0.2.1b4
+++++++

Discovery section gating and architecture task tracking
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Reliable section completion gate** — replaced heuristic phrase
  matching (``_is_section_done()``) with an explicit AI confirmation
  step.  Sections only advance when the AI responds with "Yes",
  eliminating false-positive checkmarks from transitional language.
* **"All topics covered" accuracy** — the message now only appears
  when every section received explicit AI confirmation.  Otherwise a
  softer prompt is shown.
* **"continue" keyword** — users can type ``continue`` (in addition
  to ``done``) to proceed from discovery to architecture generation.
* **Architecture sections in task tree** — ``_generate_architecture_sections()``
  now reports each section to the TUI task tree with ``in_progress`` /
  ``completed`` status updates.  Dynamically discovered sections
  (``[NEW_SECTION]`` markers) are appended in real time.
* **Timer format** — elapsed times >= 60 s now display as ``1m04s``
  instead of ``64s`` in the TUI info bar and per-section console
  output.

TUI console color, wrapping, and section pagination
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Color consolidation** — all color constants now live in ``theme.py``
  as the single source of truth.  Duplicate theme dicts in ``console.py``
  and hardcoded hex colors in ``task_tree.py``, ``tui_adapter.py``, and
  ``console.py`` toolbar functions replaced with ``COLORS`` imports.
* **Rich markup preservation** — ``TUIAdapter.print_fn()`` no longer
  strips Rich markup tags.  Messages containing ``[success]``,
  ``[info]``, etc. are routed to the new ``ConsoleView.write_markup()``
  method so status messages retain their colors in the TUI.
* **Horizontal wrapping** — ``ConsoleView`` (``RichLog``) now passes
  ``wrap=True``, eliminating the horizontal scrollbar for long lines.
* **Agent response rendering** — new ``TUIAdapter.response_fn()``
  renders agent responses as colored Markdown via
  ``ConsoleView.write_agent_response()``.  Wired through
  ``DiscoverySession.run()`` → ``DesignStage.execute()`` →
  ``StageOrchestrator._run_design()``.
* **Section pagination** — multi-section agent responses (split on
  ``## `` headings) are shown one section at a time with an "Enter to
  continue" prompt between them.  Single-section responses render all
  at once.
* **Empty submit support** — ``PromptInput.enable(allow_empty=True)``
  allows submitting with no text, used by the pagination "Enter to
  continue" prompt.  Empty submissions are not echoed to the console.
* **Clean Ctrl+C exit** — ``_run_tui()`` helper in ``custom.py``
  suppresses ``SIGINT`` during the Textual run so Ctrl+C is handled
  exclusively as a key event.  Prevents ``KeyboardInterrupt`` from
  propagating to the Azure CLI framework and eliminates the Windows
  "Terminate batch job (Y/N)?" prompt from ``az.cmd``.

Build-deploy stage decoupling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Stable stage IDs** — build stages now carry a persistent ``id``
  field (slug derived from name, e.g. ``"data-layer"``).  IDs survive
  renumbering, stage insertion/removal, and design iteration.  Legacy
  state files are backfilled on load.
* **Build-deploy correspondence** — deploy stages link back to build
  stages via ``build_stage_id`` instead of fragile stage numbers.
  ``sync_from_build_state()`` performs smart reconciliation: matching
  stages are updated while preserving deploy progress, new build stages
  create new deploy stages, and removed build stages are marked
  ``"removed"``.
* **Stage splitting** — ``split_stage(N, substages)`` replaces one
  deploy stage with N substages (``5a``, ``5b``, ``5c``) sharing the
  same ``build_stage_id``.  Supports code splits (Type A), deploy-only
  splits (Type B), and manual step insertion (Type C).
* **Manual deployment steps** — stages with ``deploy_mode: "manual"``
  display instructions and pause for user confirmation (Done / Skip /
  Need help) instead of executing IaC commands.  Manual steps can
  originate from the architect during plan derivation or from
  remediation.
* **New deploy statuses** — ``"removed"`` (build stage deleted),
  ``"destroyed"`` (resources torn down), ``"awaiting_manual"`` (waiting
  for user confirmation).
* **New slash commands** — ``/split N`` (interactive stage splitting),
  ``/destroy N`` (resource destruction with confirmation),
  ``/manual N "instructions"`` (add/view manual step instructions).
* **Compound stage references** — all stage-referencing commands
  (``/deploy``, ``/rollback``, ``/redeploy``, ``/plan``, ``/describe``)
  accept substage labels: ``/deploy 5a``, ``/rollback 5`` (all
  substages in reverse order).
* **Re-entry sync** — when the deploy session re-enters with an
  existing deploy state, it syncs with the latest build state and
  reports changes (new stages, removed stages, updated code).
* **Display improvements** — removed stages show with strikethrough and
  ``(Removed)`` suffix, manual steps show ``[Manual]`` badge, substages
  display compound IDs (``2a``, ``2b``).

Deploy auto-remediation
~~~~~~~~~~~~~~~~~~~~~~~~
* **Automatic deploy failure remediation** — when a deployment stage
  fails, the system now automatically diagnoses (QA engineer),
  determines a fix strategy (cloud architect), regenerates the code
  (IaC/app agent), and retries deployment — up to 2 remediation
  attempts before falling through to the interactive loop.
* **Downstream impact tracking** — after fixing a stage, the
  architect checks whether downstream stages need regeneration
  due to changed outputs or dependencies.  Affected stages are
  automatically regenerated before their deploy.
* **Consistent QA routing** — ``/deploy N`` and ``/redeploy N``
  slash commands now route through the remediation loop on failure,
  not just print the error.
* **Deploy state enhancements** — new ``remediating`` status,
  per-stage ``remediation_attempts`` counter, ``add_patch_stages()``,
  and ``renumber_stages()`` methods.

Incremental build stage
~~~~~~~~~~~~~~~~~~~~~~~~
* **Design change detection** — ``BuildState`` now stores a design
  snapshot (architecture hash + full text) after each build.  On
  re-entry, the build session compares the current design against the
  snapshot to determine whether regeneration is needed.
* **Three-branch Phase 2** — the deployment plan derivation phase now
  has three paths:

  - **Branch A** (first build): derive a fresh plan and save the
    design snapshot.
  - **Branch B** (design changed): ask the architect agent to diff the
    old and new architectures, classify each stage as unchanged /
    modified / removed, identify new services, and apply targeted
    updates (``mark_stages_stale``, ``remove_stages``, ``add_stages``).
    When ``plan_restructured`` is flagged, the user is offered a full
    plan re-derive.
  - **Branch C** (no changes): report "Build is up to date" and skip
    directly to the review loop.

* **Incremental stage operations** on ``BuildState``:
  ``set_design_snapshot()``, ``design_has_changed()``,
  ``get_previous_architecture()``, ``mark_stages_stale()``,
  ``remove_stages()``, ``add_stages()``, ``renumber_stages()``.
* **Architecture diff via architect agent** —
  ``_diff_architectures()`` sends old/new architecture + existing
  stages to the architect, parses JSON classification, and falls back
  to marking all stages as modified when the architect is unavailable.
* **Legacy build compatibility** — builds without a design snapshot
  (pre-incremental) are treated as "design changed" with all stages
  marked for rebuild, preserving conversation history.

TUI dashboard
~~~~~~~~~~~~~~
* **Added Textual TUI dashboard** — ``az prototype launch`` opens a full
  terminal UI with four panels: scrollable console output (RichLog),
  collapsible task tree with async status updates, growable multi-line
  prompt (Enter to submit, Shift+Enter for newline), and an info bar
  showing assist text and token usage.
* **Stage orchestrator** — the TUI auto-detects the current project stage
  from ``.prototype/state/`` files and launches the appropriate session.
  Users can navigate between design, build, and deploy without exiting.
* **Session-TUI bridge** — ``TUIAdapter`` connects synchronous sessions to
  the async Textual event loop using ``call_from_thread`` and
  ``threading.Event``.  Sessions run on worker threads with ``input_fn``
  and ``print_fn`` routed through TUI widgets.
* **Spinner → task tree** — ``_maybe_spinner`` on all four sessions
  (discovery, build, deploy, backlog) now accepts a ``status_fn`` callback
  so the TUI can show progress via the info bar instead of Rich spinners.
* **Guarded console calls** — discovery slash commands (``/open``,
  ``/status``, ``/why``, ``/summary``, ``/restart``, ``/help``) and
  design stage header now route through ``_print`` when ``input_fn`` /
  ``print_fn`` are injected, preventing Rich output conflicts in TUI mode.
* **New dependency** — ``textual>=8.0.0``.
* **Design command launches TUI** — ``az prototype design`` now opens the
  TUI dashboard and auto-starts the design session, instead of running
  synchronously in the terminal.  ``--status`` remains CLI-only.
  Artifact paths are resolved to absolute before the TUI takes over.
* **Section headers as tree branches** — during discovery, the
  biz-analyst's AI responses are scanned for ``##`` / ``###`` headings
  (e.g. "Project Context & Scope", "Data & Content") which appear as
  collapsible sub-nodes under the Design branch in the task tree.
  Duplicate headings are deduplicated by slug.

Natural language intent detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **AI-powered command classification** — all four interactive sessions
  (discovery, build, deploy, backlog) now accept natural language
  instead of slash commands.  When an AI provider is available, a
  lightweight classification call maps user input to the appropriate
  command.  Falls back to keyword/regex scoring when AI is unavailable.
* **Mid-session file reading** — ``"read artifacts from <path>"``
  reads files (PDF, DOCX, PPTX, images, text) during any session and
  injects the content into the conversation context.
* **Deploy session natural language** — the deploy session no longer
  requires slash commands.  ``"deploy stage 3"``, ``"rollback all"``,
  ``"deploy stages 3 and 4"`` are interpreted and executed directly.
* **Stage description command** — new ``/describe N`` command in both
  build and deploy sessions.  Natural language variants like
  ``"describe stage 3"`` or ``"what's being deployed in stage 2"``
  show detailed resource, file, and status information for a stage.
* **Project summary in TUI** — the welcome banner now shows a
  one-line project summary extracted from discovery state or the
  design architecture.

Packaging
~~~~~~~~~~
* **Added ``__init__.py`` to data-only directories** — 15 data directories
  (policies, standards, templates, knowledge, agent definitions) lacked
  ``__init__.py``, causing setuptools "Package would be ignored" warnings
  during wheel builds.  The ``templates/`` directory also contained Python
  modules (``registry.py``, ``validate.py``) that were not included in the
  wheel.  All data directories now have ``__init__.py`` so ``find_packages()``
  discovers them correctly.
* **Excluded ``__pycache__`` from package discovery** — ``setup.py`` now
  filters ``__pycache__`` directories from ``find_packages()`` results to
  prevent spurious build warnings.

0.2.1b3
+++++++

Build stage
~~~~~~~~~~~~
* **Removed terraform validation from build** — ``terraform init`` and
  ``terraform validate`` no longer run during the build stage.  Build
  only generates code; the deploy stage is the correct place to validate
  and execute IaC tooling.  This removes the requirement for terraform
  to be installed at build time.

0.2.1b2
+++++++

_No changes._ Utility version bump for Azure CLI library deployment.

0.2.1b1
+++++++

Azure CLI extension index compatibility
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Renamed ``--verbose`` to ``--detailed``** — ``--verbose`` / ``-v`` is a
  reserved Azure CLI global argument.  The ``prototype status``,
  ``agent list``, and ``agent show`` commands now use ``--detailed`` / ``-d``
  instead.
* **Renamed ``--output`` to ``--output-file``** — ``--output`` / ``-o`` is a
  reserved Azure CLI global argument.  The ``agent export`` command now uses
  ``--output-file`` / ``-f`` instead.
* **Consolidated deploy subcommands into flags** — ``deploy outputs``,
  ``deploy rollback-info``, and ``deploy generate-scripts`` were subcommands
  that made ``deploy`` both a command and a command group, causing an
  argparse conflict on Python 3.13.  They are now flags on the single
  ``az prototype deploy`` command: ``--outputs``, ``--rollback-info``,
  ``--generate-scripts`` (with ``--script-type``, ``--script-resource-group``,
  ``--script-registry``).
* **Dropped non-PEP 440 version suffixes from wheel filenames** — release
  and CI pipelines no longer rename wheels with ``-preview`` or ``-ci.N``
  suffixes, which broke ``azdev linter`` filename validation.
* **Fixed ``publish-index`` idempotency** — the release pipeline now checks
  out an existing PR branch instead of failing on ``git checkout -b`` when
  the branch already exists.  PR creation falls back to ``gh api`` REST
  update when a PR already exists (avoids ``read:org`` scope requirement
  of ``gh pr edit`` GraphQL).
* **Excluded ``tests`` from wheel** — ``find_packages()`` now uses
  ``exclude=["tests", "tests.*"]`` to avoid packaging the test suite.

0.2.1-preview
++++++++++++++

Quiet output by default
~~~~~~~~~~~~~~~~~~~~~~~~~
* **Suppressed JSON output** — all ``az prototype`` commands now return
  ``None`` by default, eliminating the verbose JSON dump that Azure CLI
  auto-serializes after every command.  Pass ``--json`` / ``-j`` to any
  command to restore machine-readable JSON output.
* **Global ``--json`` flag** — registered on the ``prototype`` parent
  command group so it is inherited by every subcommand without per-command
  boilerplate.
* **Console output for data commands** — ``config show`` prints
  YAML-formatted config, ``config get`` prints key/value pairs,
  ``config set`` confirms the new value, ``deploy outputs`` and
  ``deploy rollback-info`` print human-readable summaries when ``--json``
  is not supplied.

Build ``--reset`` directory cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Clean generated output on reset** — ``az prototype build --reset``
  now removes the ``concept/infra``, ``concept/apps``, ``concept/db``,
  and ``concept/docs`` directories before regenerating.  Previously only
  the build state metadata was cleared, leaving stale files that could
  cause Terraform/Bicep deployment failures when merged with new output.

Test fixes
~~~~~~~~~~~
* **Updated model defaults** — test expectations aligned with the
  ``claude-sonnet-4`` default (was ``claude-sonnet-4.5``) and version
  ``0.2.0`` (was ``0.1.1``).
* **75+ test call-sites updated** — all tests that assert on command
  return values now pass ``json_output=True`` to work with the new
  quiet-output decorator.

0.2.0-preview
++++++++++++++

Azapi provider for Terraform
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Switched from ``azurerm`` to ``azapi``** — all Terraform resources are now
  generated as ``azapi_resource`` with ARM resource types in the ``type``
  property (e.g. ``Microsoft.Storage/storageAccounts@2025-06-01``).  This
  eliminates dependency on provider-specific resource schemas and gives
  day-zero coverage for any Azure service.
* **Centralized version constants** — ``requirements.py`` declares
  ``_AZURE_API_VERSION = "2025-06-01"`` and ``_AZAPI_PROVIDER_VERSION = "2.8.0"``
  with ``get_dependency_version()`` lookup.  Both agents read these at runtime.
* **Provider pin injection** — ``TerraformAgent.get_system_messages()`` injects
  the exact ``required_providers`` block with pinned ``azure/azapi ~> 2.8.0``
  into the agent's system context.
* **ARM REST API body structure** — resource properties go in a ``body`` block
  using the ARM REST API schema.  Managed identities and RBAC role assignments
  are also ``azapi_resource`` declarations.
* **Cross-stage references** — use ``data "azapi_resource"`` with
  ``resource_id`` variables instead of hardcoded names or ``terraform_remote_state``.
* **``versions.tf`` blocked** — ``_BLOCKED_FILES`` in the build session
  prevents generation of ``versions.tf``; all provider configuration must go in
  ``providers.tf`` to avoid Terraform's "duplicate required_providers" error.

Azapi-aligned Bicep generation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Pinned Azure API version for Bicep** — ``BicepAgent.get_system_messages()``
  injects the same ``_AZURE_API_VERSION`` so all resource type declarations use
  a consistent API version (e.g. ``Microsoft.Storage/storageAccounts@2025-06-01``).
* **Azure Verified Modules** — Bicep agent prefers AVM modules from the public
  Bicep registry where available.
* **Learn docs reference** — agent prompt includes the URL pattern for Azure
  ARM template reference docs with ``?pivots=deployment-language-bicep``.

Enterprise Copilot endpoint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Migrated to ``api.enterprise.githubcopilot.com``** — the enterprise endpoint
  exposes the full model catalogue (Claude, GPT, and Gemini families) whereas
  the public endpoint only returned a subset of GPT models.
* **``COPILOT_BASE_URL`` env var** — allows overriding the base URL for
  testing or on-premises environments.
* **Dynamic model discovery** — ``CopilotProvider.list_models()`` queries
  the ``/models`` endpoint at runtime; falls back to a curated list on failure.
* **Default model changed** — ``claude-sonnet-4`` replaces ``claude-sonnet-4.5``
  as the default across the Copilot provider and factory.
* **Timeout increased** — default request timeout raised from 120 s to 300 s
  to accommodate large architecture generation prompts.
* **Editor headers updated** — ``User-Agent``, ``Copilot-Integration-Id``,
  ``Editor-Version``, and ``Editor-Plugin-Version`` now match the official
  Copilot CLI (``copilot/0.0.410``).
* **Gemini routing** — ``_COPILOT_ONLY_PREFIXES`` in ``factory.py`` now
  includes ``"gemini-"`` alongside ``"claude-"``, enforcing that Gemini models
  are only routed via the Copilot provider.

Model catalogue expansion
~~~~~~~~~~~~~~~~~~~~~~~~~~
* **MODELS.md** — comprehensive model reference documenting all three provider
  families:

  - **Anthropic Claude** (8 models): Sonnet 4 / 4.5 / 4.6, Opus 4.5 / 4.6 /
    4.6-fast / 4.6-1m, Haiku 4.5.
  - **OpenAI GPT** (10 models): GPT-5.3 Codex through GPT-5-mini, GPT-4.1
    (1M context), GPT-4o-mini.
  - **Google Gemini** (2 models): Gemini 3 Pro Preview, Gemini 2.5 Pro
    (1M context).

* **Per-stage model recommendations** — guidance on optimal model selection
  by stage (design, build, deploy, analyze, docs).
* **Provider comparison table** — authentication, data residency, SLA, and
  cost comparison across copilot, github-models, and azure-openai.

Per-stage QA with remediation loop
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Automatic per-stage QA** — infra, data, integration, and app stages now
  receive QA review immediately after generation (not just at the end of the
  build).
* **Remediation loop** — when QA identifies issues, the IaC agent regenerates
  the affected stage with QA findings appended as fix instructions.  Up to 2
  remediation attempts per stage (``_MAX_STAGE_REMEDIATION_ATTEMPTS``).
* **Inline Terraform validation** — ``_validate_terraform_stage()`` runs
  ``terraform init -backend=false`` + ``terraform validate`` per stage; errors
  are surfaced as ``## Terraform Validation Error (MUST FIX)`` in the QA task.
* **Advisory QA pass** — after all stages pass per-stage QA, an additional
  high-level advisory review runs (security, scalability, cost, production
  readiness).  Advisory findings are informational only — no regeneration.
* **Knowledge contributions** — QA findings are automatically submitted to the
  knowledge base (fire-and-forget) after both per-stage and advisory reviews.

QA engineer enhancements
~~~~~~~~~~~~~~~~~~~~~~~~~
* **Azapi-aware review** — QA agent validates that all Terraform resources use
  ``azapi_resource`` with the correct API version in the ``type`` property.
* **Mandatory review checklist** — authentication & identity completeness,
  cross-stage reference correctness, script completeness (``set -euo pipefail``,
  error handling, output export), output completeness, structural consistency,
  code completeness, and Terraform file structure (single ``terraform {}``
  block in ``providers.tf``).
* **Image/screenshot support** — ``execute_with_image()`` accepts vision API
  input for analyzing error screenshots; falls back to text-only if vision
  fails.

Tool-calling support across all providers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **``ToolCall`` dataclass** — new first-class abstraction in ``provider.py``
  with ``id``, ``name``, and ``arguments`` fields.
* **``AIMessage`` extensions** — ``tool_calls: list[ToolCall] | None`` for
  assistant messages requesting tool invocations, ``tool_call_id: str | None``
  for tool result messages (``role="tool"``).
* **``AIProvider.chat(tools=...)``** — all three providers (copilot,
  github-models, azure-openai) accept OpenAI function-calling format tools
  and return ``tool_calls`` in ``AIResponse``.  Fully backward compatible.
* **``_messages_to_dicts()``** — each provider now has a dedicated helper
  for serializing tool call fields into OpenAI-compatible message dicts.

0.1.1-preview
++++++++++++++

v0.1.1 polish pass
~~~~~~~~~~~~~~~~~~~
* **Unified ``_DONE_WORDS``** — all four interactive sessions (discovery,
  build, deploy, backlog) now accept ``done``, ``finish``, ``accept``, and
  ``lgtm`` as session-ending inputs.  Previously discovery/build lacked
  ``finish`` and deploy/backlog lacked ``accept``/``lgtm``.
* **Agent list updated** — help text now lists all 11 built-in agents
  (was 9; added ``security-reviewer`` and ``monitoring-agent``).
* **Bare ``print()`` eliminated** — ``deploy_stage.py`` status and reset
  paths now use ``console.print_info()`` / ``print_success()``.
  ``file_extractor.py`` verbose output uses ``print_fn`` callback.
* **Validation script output** — ``policies/validate.py`` and
  ``templates/validate.py`` now use ``sys.stdout.write()`` for consistent
  non-emoji output in CI environments.
* **Backlog state persistence** — ``BacklogSession`` now calls
  ``save()`` after generating items and after each interactive mutation.
* **Deploy state persistence** — ``DeploySession`` now calls ``save()``
  after ``mark_stage_deployed()`` so progress survives crashes.
* **Build failure feedback** — ``BuildSession`` now prints a visible
  warning before routing agent failures to QA, so the user is aware of
  the issue.
* **DEFERRED.md** — all 5 deferred items marked as completed with
  implementation references.
* **HISTORY.rst** — changelog entries added for Phases 7–10.

MCP (Model Context Protocol) integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Handler-based plugin pattern** — ``MCPHandler`` ABC in ``mcp/base.py``
  owns transport, auth, and protocol.  ``MCPHandlerConfig`` declares name,
  stage/agent scoping, timeouts, and retry limits.
* **``MCPRegistry``** — builtin/custom resolution following the same
  pattern as ``AgentRegistry``.
* **``MCPManager``** — lifecycle management with lazy connect, tool
  routing, and circuit breaker (3 consecutive errors disables a handler).
  Used as a context manager for clean shutdown.
* **OpenAI function-calling bridge** —
  ``MCPManager.get_tools_as_openai_schema()`` converts MCP tool
  definitions to the OpenAI ``tools`` format for all three AI providers.
* **AI provider tool support** — ``ToolCall`` dataclass, ``tools``
  parameter on ``AIProvider.chat()``, ``tool_calls`` on
  ``AIMessage``/``AIResponse``.  All three providers (copilot,
  github-models, azure-openai) support tool calls with backward
  compatibility.
* **Agent tool-call loop** — ``BaseAgent._enable_mcp_tools = True``
  (default) with ``_max_tool_iterations = 10``.  Agents receive scoped
  tools, detect tool calls in AI responses, invoke via ``MCPManager``,
  feed results back, and loop until the model stops calling tools.
* **Custom handler loader** — ``mcp/loader.py`` discovers handlers from
  ``.prototype/mcp/`` Python files.  Filename convention:
  ``lightpanda_handler.py`` → handler name ``lightpanda``.
* **Scoping** — per-stage (``stages: ["build", "deploy"]`` or null for
  all) and per-agent (``agents: ["terraform-agent"]`` or null for all).
* **Example handler** — ``mcp/examples/lightpanda_handler.py`` provides
  a JSON-RPC over HTTP reference implementation.
* **Configuration** — ``mcp.servers`` list in ``prototype.yaml``;
  ``mcp.servers`` in ``SECRET_KEY_PREFIXES`` for credential isolation.

Anti-pattern detection
~~~~~~~~~~~~~~~~~~~~~~~
* **Post-generation scanning** — ``governance/anti_patterns/`` detects
  common issues in generated IaC code *after* generation, independent
  of the policy engine.  User decides: accept, override, or regenerate.
* **9 domains**: security, networking, authentication, storage,
  containers, encryption, monitoring, cost, and **completeness**
  (disabled-auth-without-identity, hardcoded cross-stage refs,
  incomplete scripts).
* **API** — ``load()`` → ``list[AntiPatternCheck]``,
  ``scan(text)`` → ``list[str]``, ``reset_cache()``.
* **Governance integration** — ``governance.py`` delegates to
  ``anti_patterns.scan()`` for violation detection.
  ``reset_caches()`` clears all three governance caches (policies,
  templates, anti-patterns).

Standards system
~~~~~~~~~~~~~~~~~
* **Curated design principles & reference patterns** —
  ``governance/standards/`` provides prescriptive guidance injected
  into agent system prompts via ``_include_standards`` flag.
* **7 standards files** across 4 directories:
  ``principles/`` (design, coding), ``terraform/`` (modules),
  ``bicep/`` (modules), ``application/`` (python, dotnet).
* **Terraform standards** — TF-001 through TF-010: module structure,
  naming, variables, outputs, cross-stage remote state (TF-006),
  backend consistency (TF-007), complete outputs (TF-008), robust
  deploy.sh (TF-009), companion resources (TF-010).
* **Bicep standards** — BCP-001 through BCP-008: module structure,
  parameters, outputs, cross-stage params (BCP-006), robust deploy.sh
  (BCP-007), companion resources (BCP-008).
* **Application standards** — Python and .NET patterns for
  Azure-deployed applications.
* **Selective injection** — ``_include_standards = False`` on
  cost-analyst, qa-engineer, doc-agent, project-manager, and
  biz-analyst (non-IaC agents).
* **API** — ``load()`` → ``list[Standard]``,
  ``format_for_prompt()`` → ``str``, ``reset_cache()``.

Policy expansion
~~~~~~~~~~~~~~~~~
* **13 built-in policies** (was 9 at 0.1.0) — 4 new Azure service
  policies added: App Service, Storage, Functions, and Monitoring.

Build QA remediation loop
~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Automatic QA → IaC agent remediation** — after QA review identifies
  issues in generated code, ``_identify_affected_stages()`` determines
  which build stages are affected and regenerates them with QA findings
  appended as fix instructions.
* **Architect-first stage identification** — affected stages are
  identified by asking the architect agent first; falls back to regex
  matching on failure.
* **Re-review after remediation** — QA re-reviews remediated code and
  reports only remaining issues.  Knowledge contribution happens on the
  final QA output (after remediation).

Cross-tenant and service principal deploy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Service principal authentication** — ``--service-principal``,
  ``--client-id``, ``--client-secret``, ``--tenant-id`` parameters on
  ``az prototype deploy``.  SP credentials route to
  ``prototype.secrets.yaml`` via ``deploy.service_principal`` prefix.
* **Cross-tenant targeting** — ``--tenant`` parameter sets the
  deployment subscription context.  Preflight ``_check_tenant()`` warns
  when the active tenant differs from the target.
* **Deploy helpers** — ``login_service_principal()``,
  ``set_deployment_context()``, ``get_current_tenant()`` in
  ``deploy_helpers.py``.
* **``/login`` slash command** — runs ``az login`` interactively within
  the deploy session; suggests ``/preflight`` afterward to re-validate
  prerequisites.

Agent governance — Phases 8–10
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **QA-first error routing** — shared ``route_error_to_qa()`` function
  in ``stages/qa_router.py`` used by all four interactive sessions
  (discovery, build, deploy, backlog).  QA agent diagnoses, tokens are
  recorded, and knowledge contributions fire-and-forget.
* **Agent delegation priority** — ``registry.find_agent_for_task()``
  implements the formal priority chain from CLAUDE.md: error→QA,
  service+IaC→terraform/bicep, scope→PM, multi-service→architect,
  discovery→biz, docs→doc, cost→cost, fallback→keyword scoring→PM.
  Backward-compatible with ``find_best_for_task()``.
* **Escalation tracking** — ``EscalationTracker`` in
  ``stages/escalation.py`` persists to
  ``.prototype/state/escalation.yaml``.  Four-level chain:
  L1 (documented) → L2 (architect/PM) → L3 (web search) → L4 (human).
  ``should_auto_escalate()`` checks timeout (default 120 s).
* **Backlog ``/add`` enrichment** — PM agent creates structured items
  via ``_enrich_new_item()``; bare fallback if AI unavailable.
* **Architect-driven stage identification** —
  ``_identify_affected_stages()`` asks architect agent first, falls back
  to regex on failure.
* **Build, deploy, backlog sessions** all create ``EscalationTracker``
  in ``__init__``.

Runtime documentation access — Phase 7
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Web search skill** — agents emit ``[SEARCH: query]`` markers;
  framework intercepts and fetches results from Microsoft Learn / web
  search.  Results injected as context for the next AI call.
  Max 3 markers resolved per turn.
* **Search caching** — ``SearchCache`` in ``knowledge/search_cache.py``
  with in-memory TTL cache (30 min, 50 entries, LRU eviction).  Shared
  across agents via ``AgentContext._search_cache``.
* **POC vs. production annotations** — ``compose_context(mode="poc")``
  strips ``## Production Backlog Items`` from service knowledge files.
  ``extract_production_items(service)`` returns bullet list for backlog
  generation.
* **5 agents enabled**: cloud-architect, terraform-agent, bicep-agent,
  app-developer, qa-engineer.

Community knowledge contributions — Phase 6
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **``az prototype knowledge contribute``** — New command to submit
  knowledge base contributions as GitHub Issues.  Interactive by default;
  non-interactive via ``--service`` + ``--description`` or ``--file``.
  ``--draft`` previews without submitting.
* **``KnowledgeContributor`` module** — Module-level functions following
  ``backlog_push.py`` pattern: ``check_knowledge_gap()``,
  ``format_contribution_body/title()``, ``submit_contribution()``,
  ``build_finding_from_qa()``, ``submit_if_gap()``.
* **Auto-submission hooks** — Fire-and-forget knowledge contributions
  after QA diagnosis in deploy failures (``DeploySession``) and build
  QA review (``BuildSession``).  Silently submits when a gap is detected;
  never prompts or blocks the user.
* **GitHub Issue template** — Structured form at
  ``.github/ISSUE_TEMPLATE/knowledge-contribution.yml`` with Type,
  Target File, Section, Context, Rationale, Content to Add, and Source
  fields.  Labels: ``knowledge-contribution``, ``service/{name}``, type.

Token status display — Phase 5
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **``TokenTracker``** — Accumulates ``AIResponse.usage`` across turns.
  Tracks this-turn, session-total, and budget-percentage.  Model context
  window lookup for 11 models.
* **Session integration** — Token status rendered as dim right-justified
  line after AI responses in all 4 interactive sessions:
  ``DiscoverySession``, ``BuildSession``, ``DeploySession``,
  ``BacklogSession``.
* **``Console.print_token_status()``** — Right-justified muted text
  renderer for token usage information.

Agent quality & knowledge system — Phase 4
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **``security-reviewer`` agent** — Pre-deployment IaC scanning for RBAC
  over-privilege, public endpoints, missing encryption, hardcoded secrets.
  Reports findings as BLOCKERs (must fix) or WARNINGs (can defer).
  Knowledge-backed via ``roles/security-reviewer.md``.
* **``monitoring-agent``** — Generates Azure Monitor alerts, diagnostic
  settings, Application Insights config, and dashboards.  POC-appropriate
  (failure alerts, latency, resource health).  Knowledge-backed via
  ``roles/monitoring.md``.
* **``AgentContract``** — Formal input/output contracts on all 11 agents.
  Declares artifact dependencies (``inputs``), produced artifacts
  (``outputs``), and delegation targets (``delegates_to``).
  ``AgentOrchestrator.check_contracts()`` validates contract satisfaction.
* **Parallel execution** — ``AgentOrchestrator.execute_plan_parallel()``
  runs independent tasks concurrently via ``ThreadPoolExecutor``.
  Builds dependency graph from agent contracts; respects artifact
  ordering.  Diamond and pipeline patterns supported.
* **New capabilities** — ``SECURITY_REVIEW`` and ``MONITORING`` added to
  ``AgentCapability`` enum.  11 built-in agents (was 9).
* **58 new tests** — SecurityReviewerAgent (10), MonitoringAgent (8),
  registry integration (8), AgentContract (6+10), orchestrator contract
  validation (5), parallel execution (7), knowledge templates (4).

Agent commands hardening
~~~~~~~~~~~~~~~~~~~~~~~~
* **Rich UI for all agent commands** — ``agent list``, ``agent show``,
  ``agent add``, ``agent override``, and ``agent remove`` now use
  ``console.*`` styled output (header, success, info, dim, file_list).
* ``--json`` / ``-j`` flag on ``agent list`` and ``agent show`` returns
  raw dicts for scripting.  ``--detailed`` / ``-d`` expands capability
  details (list) or shows full system prompt (show).
* **Interactive agent creation** — ``agent add`` defaults to an
  interactive walkthrough (description, role, capabilities, constraints,
  system prompt, examples) matching the pattern of design/build/deploy.
  Non-interactive via ``--file`` or ``--definition``.
* **``agent update``** — modify custom agent properties.  Interactive
  by default with current values as defaults.  Field flags
  (``--description``, ``--capabilities``, ``--system-prompt-file``)
  for targeted non-interactive changes.
* **``agent test``** — send a test prompt to any agent, display the
  response with model and token count.  Default prompt:
  "Briefly introduce yourself and describe your capabilities."
* **``agent export``** — export any agent (including built-in) as a
  portable YAML file for sharing or customization.
* **Override validation** — ``agent override`` now verifies the file
  exists, parses as valid YAML with a ``name`` field, and warns if the
  target is not a known built-in agent.
* **Comprehensive help text** — all 8 agent commands have long-summaries
  with examples matching the depth of build/deploy/generate help.

Generate commands hardening
~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Interactive backlog session** — ``az prototype generate backlog``
  now launches a conversational session (following the build/deploy
  ``Session`` pattern) where you can review, refine, add, update, and
  remove backlog items before pushing to your provider.
* **Backlog push to GitHub** — ``/push`` creates GitHub Issues via
  ``gh`` CLI with task checklists, acceptance criteria, and effort
  labels.
* **Backlog push to Azure DevOps** — ``/push`` creates Features /
  User Stories / Tasks via ``az boards`` with parent-child linking.
* **BacklogState persistence** — backlog items, push status, and
  conversation history are stored in
  ``.prototype/state/backlog.yaml`` for re-entrant sessions.
* **Scope-aware backlog** — in-scope items become stories,
  out-of-scope items are excluded, and deferred items get a separate
  "Deferred / Future Work" epic.
* **``--quick`` flag** — lighter generate → confirm → push flow
  without the interactive loop.
* **``--refresh`` flag** — force fresh AI generation, bypassing
  cached items.
* **``--status`` flag** — show current backlog state without
  starting a session.
* **``--push`` flag** — in quick mode, auto-push after generation.
* **AI-populated docs/speckit** — when design context is available,
  the doc-agent fills template ``[PLACEHOLDER]`` values with real
  content from the architecture.  Falls back to static rendering
  if no design context or AI is unavailable.
* **Rich UI** for ``generate docs``, ``generate speckit``, and
  ``generate backlog`` — bare ``print()`` / emoji replaced with
  ``console.print_header()``, ``print_success()``, ``print_info()``,
  ``print_file_list()``, and ``print_dim()``.
* **Project manager scope awareness** — agent prompt updated with
  scope boundary rules for in-scope, out-of-scope, and deferred
  items.
* **Telemetry overrides** — ``backlog_provider``, ``output_format``,
  and ``items_pushed`` attached to backlog command telemetry.

Init command hardening
~~~~~~~~~~~~~~~~~~~~~~
* ``--location`` is now **required** (no default). Enforced with
  ``CLIError`` when missing.
* ``--environment`` parameter added (``dev`` / ``staging`` / ``prod``,
  default ``dev``). Sets ``project.environment`` in config.
* ``--model`` parameter added. Overrides the provider-based default
  model (``claude-sonnet-4.5`` for copilot, ``gpt-4o`` for others).
* **Idempotency check** — if the target directory already contains a
  ``prototype.yaml``, the user is prompted before overwriting.
* **Conditional GitHub auth** — ``gh`` authentication and Copilot
  license validation are skipped when ``--ai-provider azure-openai``
  is selected. The ``gh_installed`` guard is no longer unconditional.
* **Rich UI** — bare ``print()`` / emoji output replaced with
  ``console.print_header()``, ``print_success()``, ``print_warning()``,
  ``print_file_list()``, and a summary ``panel()`` at completion.

Config commands
~~~~~~~~~~~~~~~
* **``az prototype config get``** — new command to retrieve a single
  configuration value by dot-separated key. Secret values are masked
  as ``***``.
* **``config show`` secret masking** — values stored in
  ``prototype.secrets.yaml`` (API keys, subscription IDs, tokens) are
  now masked as ``***`` in the output.
* **``config init`` marks init complete** — ``stages.init.completed``
  and timestamp are now set when using ``config init``, so downstream
  guards pass without requiring ``az prototype init``.
* **``config init`` Rich UI** — bare ``print()`` / emoji replaced
  with ``console.print_header()``, ``print_info()``, ``print_dim()``,
  ``panel()``, ``print_success()``, and ``print_file_list()``.
* **``config set`` validation** — ``project.iac_tool`` (must be
  ``terraform`` or ``bicep``) and ``project.location`` (must be a
  known Azure region) are now validated at set time with helpful error
  messages.

Enriched status command
~~~~~~~~~~~~~~~~~~~~~~~
* ``az prototype status`` now reads all three stage state files
  (``discovery.yaml``, ``build.yaml``, ``deploy.yaml``) to display
  real progress — not just boolean completion flags.
* **Default mode** — Rich console summary showing project config,
  per-stage progress with counts (exchanges, confirmed items, stages
  accepted, files generated, stages deployed/failed/rolled back), and
  pending file changes.
* ``--detailed`` / ``-d`` — expanded per-stage detail using existing
  state formatters (open/confirmed items, build stage breakdown,
  deploy stage status, deployment history).
* ``--json`` / ``-j`` — enriched machine-readable dict (superset of
  old format) with new fields: ``environment``, ``naming_strategy``,
  ``project_id``, ``deployment_history``, and per-stage detail counts.
* Surfaces previously hidden config fields: project ID, environment,
  and naming strategy.
* Deployment history from ``ChangeTracker`` included in output.

Telemetry enhancements
~~~~~~~~~~~~~~~~~~~~~~
* ``parameters`` field — the ``@track`` decorator now forwards
  sanitized command kwargs as a JSON-serialised dict.  Sensitive
  values (``subscription``, ``token``, ``api_key``, ``password``,
  ``secret``, ``key``, ``connection_string``) are redacted to
  ``***`` before transmission.
* ``error`` field — on command failure the exception type and
  message are captured (e.g. ``CLIError: Resource group not found``)
  and sent alongside ``success=false``, truncated to 1 KB.
* Both fields are conditional — omitted from the envelope when
  empty, so successful commands incur no additional payload.
* **Interactive command telemetry** — the ``@track`` decorator now
  reads ``cmd._telemetry_overrides`` (a ``dict``) so that commands
  which collect values via interactive prompts (e.g. ``config init``)
  can forward the chosen values to telemetry.  Overrides take
  precedence over kwargs and are merged into the ``parameters`` field.
* ``init`` and ``config init`` now attach resolved configuration
  values (``location``, ``ai_provider``, ``model``, ``iac_tool``,
  ``environment``, and for ``config init`` also ``naming_strategy``)
  as telemetry overrides after execution / interactive wizard
  completes.

Analyze command hardening
~~~~~~~~~~~~~~~~~~~~~~~~~
* ``analyze costs`` results are now **cached** in
  ``.prototype/state/cost_analysis.yaml``.  Re-running the command
  returns the cached result unless the design context changes.
  Use ``--refresh`` to force a fresh analysis.
* AI temperatures lowered to 0.0 in the cost analyst agent for
  deterministic output.
* Rich UI for ``analyze error`` and ``analyze costs`` — emoji-free
  styled output using ``console.print_header()``, ``print_info()``,
  ``print_success()``, and ``print_agent_response()``.
* ``analyze error`` shows a soft warning when no design context is
  available (analysis still proceeds with reduced accuracy).
* ``_load_design_context()`` now checks 3 sources in priority order:
  ``design.json``, ``discovery.yaml`` (via ``DiscoveryState``), then
  ``ARCHITECTURE.md``.  Previously only checked source 1 and 3.

Deploy subcommand hardening
~~~~~~~~~~~~~~~~~~~~~~~~~~~
* Rich UI for ``deploy outputs``, ``deploy rollback-info``, and
  ``deploy generate-scripts`` — emoji-free styled output using
  ``console.*`` methods.
* Empty-state warnings for ``deploy outputs`` and
  ``deploy rollback-info`` when no deployment data exists.

0.1.0-preview
++++++++++++++

**Initial release** of the ``az prototype`` Azure CLI extension — an
AI-driven prototyping engine that takes you from idea to deployed Azure
infrastructure in four stages: ``init → design → build → deploy``.

Stage pipeline
~~~~~~~~~~~~~~
* Four-stage workflow: **init**, **design**, **build**, **deploy** — each
  re-entrant with prerequisite guards and persistent state tracking.
* Organic, multi-turn **discovery conversation** with joint
  ``biz-analyst`` + ``cloud-architect`` perspectives in a single session
  — captures requirements with architectural feasibility feedback.
* **Cost awareness** during discovery — surfaces pricing models and
  relative cost comparisons when discussing Azure service choices.
* **Template-aware discovery** — suggests matching workload templates
  when user requirements align with a known pattern.
* **Explicit prototype scoping** — tracks in-scope, out-of-scope, and
  deferred items throughout discovery for downstream backlog and
  documentation generation.
* **Structured requirements extraction** — heading-based parser reliably
  extracts goals, requirements, constraints, scope, and services from
  the agent summary.
* Interactive design refinement loop with ``--interactive`` flag.
* **Binary artifact support** — ``--artifacts`` accepts PDF, DOCX, PPTX,
  XLSX, and image files; documents have text extracted and embedded
  images sent via the vision API.

Agent system
~~~~~~~~~~~~
* **9 built-in agents**: cloud-architect, biz-analyst, app-developer,
  bicep-agent, terraform-agent, doc-agent, qa-engineer, cost-analyst,
  project-manager.
* Three-tier agent resolution: **custom → override → built-in** — users
  can replace or extend any agent via YAML or Python definitions.
* ``az prototype agent`` command group for listing, adding, overriding,
  showing, and removing agents.

AI providers
~~~~~~~~~~~~
* **GitHub Models**, **Azure OpenAI**, and **GitHub Copilot** backends
  with provider allowlisting — non-Azure providers are blocked.
* Streaming support for all providers.
* Managed identity and API key authentication for Azure OpenAI.
* Copilot Business / Enterprise license validation.

Policy-driven governance
~~~~~~~~~~~~~~~~~~~~~~~~
* ``PolicyEngine`` loads ``*.policy.yaml`` files with severity levels
  (required / recommended / optional) across 6 categories.
* **9 built-in policies**: Container Apps, Cosmos DB, Key Vault, SQL
  Database, Managed Identity, Network Isolation, APIM-to-Container-Apps,
  Authentication, Data Protection.
* ``GovernanceContext`` automatically injects compact policy summaries
  into every agent's system prompt — agents are governance-aware by
  default.
* Policy conflicts surfaced during discovery; user may accept or
  override with full audit tracking.
* Custom policies via ``.prototype/policies/`` directory.

Workload templates
~~~~~~~~~~~~~~~~~~
* **5 built-in templates**: web-app, serverless-api, microservices,
  ai-app, data-pipeline — each defines Azure services, connections,
  defaults, and requirements seeds.
* Template schema validation (``template.schema.json``).
* Custom templates via ``.prototype/templates/`` directory.

Interactive deploy stage
~~~~~~~~~~~~~~~~~~~~~~~~
* **Interactive by default** — Claude Code-inspired bordered prompts,
  progress indicators, and conversational deployment session following
  the ``BuildSession`` pattern.
* **7-phase orchestration**: load build state → plan overview →
  preflight checks → stage-by-stage deploy → output capture → deploy
  report → interactive loop.
* **Preflight checks** — validates subscription, IaC tool (Terraform
  or Bicep), resource group, and required Azure resource providers
  before deploying; surfaces fix commands for common issues.
* **Deploy state persistence** — ``DeployState`` (YAML at
  ``.prototype/state/deploy.yaml``) tracks per-stage deployment
  status, preflight results, deploy/rollback audit trail, captured
  outputs, and conversation history.  Supports ``--reset`` to clear
  and ``--status`` to display progress without starting a session.
* **Ordered rollback** — cannot roll back stage N while a higher-
  numbered stage is still deployed; ``/rollback all`` enforces
  reverse order automatically.
* **QA-first error routing** — deployment failures route to
  ``qa-engineer`` for diagnosis before offering retry/skip/rollback.
* **Slash commands** during deploy: ``/status``, ``/stages``,
  ``/deploy [N|all]``, ``/rollback [N|all]``, ``/redeploy N``,
  ``/plan N``, ``/outputs``, ``/preflight``, ``/help``.
* **Dry-run mode** — ``--dry-run`` runs Terraform plan / Bicep
  What-If without executing; combinable with ``--stage N`` for
  per-stage preview.
* **Single-stage deploy** — ``--stage N`` deploys one stage
  non-interactively.
* **Output capture** — persists Terraform / Bicep outputs to JSON and
  exports ``PROTOTYPE_*`` environment variables.
* **Deploy script generation** — auto-generates ``deploy.sh`` for
  webapp, container-app, and function deploy types.
* **Rollback primitives** — ``terraform destroy`` and Bicep resource
  deletion with pre-deploy snapshots.

Documentation & analysis
~~~~~~~~~~~~~~~~~~~~~~~~
* **6 doc templates**: ARCHITECTURE, AS_BUILT, COST_ESTIMATE,
  DEPLOYMENT, CONFIGURATION, DEVELOPMENT — generated via ``doc-agent``.
* ``az prototype generate speckit`` — full spec-kit documentation
  bundle.
* ``az prototype generate backlog`` — generates user stories from
  architecture.
* ``az prototype analyze error`` — AI-powered error diagnosis with
  fix recommendations.
* ``az prototype analyze costs`` — cost estimation at Small / Medium /
  Large t-shirt sizes.

Configuration & naming
~~~~~~~~~~~~~~~~~~~~~~
* ``ProjectConfig`` manages ``prototype.yaml`` + ``prototype.secrets.yaml``
  (git-ignored) with Azure-only endpoint validation and sensitive-key
  isolation.
* ``az prototype config init`` — interactive setup wizard.
* **4 naming strategies**: Microsoft ALZ, Microsoft CAF, simple,
  enterprise — plus fully custom patterns for consistent resource naming.

Interactive build stage
~~~~~~~~~~~~~~~~~~~~~~~
* **Interactive by default** — Claude Code-inspired bordered prompts,
  spinners, progress indicators, and conversational review loop.
* **Fine-grained deployment staging** — each infrastructure component,
  database system, and application gets its own dependency-ordered stage.
* **Template matching** — workload templates are optional starting points
  scored by service overlap with the design architecture (>30% threshold);
  multiple templates can match; empty match is valid.
* **Computed resource names** — each service in the deployment plan
  carries its resolved name (via naming strategy), ARM resource type,
  and SKU.
* **Per-stage policy enforcement** — ``PolicyResolver`` checks generated
  code against governance policies after each stage; violations resolved
  conversationally (accept compliant / override with justification /
  regenerate).
* **Build state persistence** — ``BuildState`` (YAML at
  ``.prototype/state/build.yaml``) tracks deployment plan, generation
  log, policy checks, overrides, review decisions, and conversation
  history.  Supports ``--reset`` to clear and ``--status`` to display
  progress without starting a session.
* **QA review** — cross-cutting QA agent review of all generated code
  after staged generation completes.
* **Build report** — styled summary showing templates used, IaC tool,
  per-stage status (files, resources, policy results), and totals.
* **Review loop** — feedback targets specific stages or cross-cutting
  concerns; AI regenerates affected stages with policy re-check.
* **Slash commands** during build: ``/status``, ``/stages``, ``/files``,
  ``/policy``, ``/help``, ``done`` / ``accept``, ``quit``.
* **Multi-resource telemetry** — ``track_build_resources()`` sends array
  of ``{resourceType, sku}`` pairs with backward-compatible scalar
  fields for the first resource.

Telemetry
~~~~~~~~~
* Application Insights integration (``opencensus-ext-azure``) with
  ``@track`` decorator on all commands.
* Fields: ``commandName``, ``tenantId``, ``provider``, ``model``,
  ``resourceType``, ``location``, ``sku``, ``extensionVersion``,
  ``success``, ``timestamp``.
* Multi-resource support via ``track_build_resources()`` for build
  commands with multiple Azure resources.
* Honours ``az config set core.collect_telemetry=no`` opt-out.
* Graceful degradation — telemetry failures are always silent.
