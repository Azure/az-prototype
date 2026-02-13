.. :changelog:

Release History
===============

0.1.0
++++++

**Initial release** of the ``az prototype`` Azure CLI extension — an
AI-driven prototyping engine that takes you from idea to deployed Azure
infrastructure in four stages: ``init → design → build → deploy``.

Stage pipeline
~~~~~~~~~~~~~~
* Four-stage workflow: **init**, **design**, **build**, **deploy** — each
  re-entrant with prerequisite guards and persistent state tracking.
* Organic, multi-turn **discovery conversation** (``biz-analyst`` agent)
  that captures requirements before architecture generation.
* Interactive design refinement loop with ``--interactive`` flag.

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
* **7 built-in policies**: Container Apps, Cosmos DB, Key Vault, SQL
  Database, Managed Identity, Network Isolation, APIM-to-Container-Apps.
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

Deployment
~~~~~~~~~~
* **Incremental deploys** — SHA-256 change tracking deploys only what
  changed; ``--force`` for full redeployment.
* Scoped deployments (``all``, ``infra``, ``apps``, ``db``, ``docs``)
  and single-stage targeting with ``--stage N``.
* ``--plan-only`` mode for deployment plan preview.
* **Bicep What-If** preview via ``az deployment group what-if``.
* **Output capture** — persists Terraform / Bicep outputs to JSON and
  exports ``PROTOTYPE_*`` environment variables.
* **Deploy script generation** — auto-generates ``deploy.sh`` for
  webapp, container-app, and function deploy types.
* **Rollback support** — pre-deploy snapshots with rollback
  instructions for both Terraform and Bicep.

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

Telemetry
~~~~~~~~~
* Application Insights integration (``opencensus-ext-azure``) with
  ``@track`` decorator on all commands.
* Fields: ``commandName``, ``tenantId``, ``provider``, ``model``,
  ``resourceType``, ``location``, ``sku``, ``extensionVersion``,
  ``success``, ``timestamp``.
* Honours ``az config set core.collect_telemetry=no`` opt-out.
* Graceful degradation — telemetry failures are always silent.
