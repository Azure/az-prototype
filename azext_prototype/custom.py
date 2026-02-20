"""Custom command implementations for az prototype.

These functions are the entry points called by the Azure CLI framework.
Each one maps to a registered command in commands.py.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from knack.util import CLIError

from azext_prototype.telemetry import track

logger = logging.getLogger(__name__)


# ======================================================================
# Helpers
# ======================================================================

def _get_project_dir() -> str:
    """Resolve the current project directory."""
    return str(Path.cwd().resolve())


def _load_config(project_dir: str | None = None):
    """Load project configuration."""
    from azext_prototype.config import ProjectConfig

    project_dir = project_dir or _get_project_dir()
    config = ProjectConfig(project_dir)
    config.load()
    return config


def _build_registry(config=None, project_dir: str | None = None):
    """Build the agent registry with built-in and custom agents."""
    from azext_prototype.agents.registry import AgentRegistry
    from azext_prototype.agents.builtin import register_all_builtin
    from azext_prototype.agents.loader import load_agents_from_directory

    registry = AgentRegistry()

    # 1. Register built-in agents
    register_all_builtin(registry)

    # 2. Load custom agents from project directory
    if project_dir and config:
        custom_dir = config.get("agents.custom_dir", ".prototype/agents/")
        custom_path = str(Path(project_dir) / custom_dir)
        custom_agents = load_agents_from_directory(custom_path)
        for agent in custom_agents:
            registry.register_custom(agent)

        # 3. Apply overrides
        overrides = config.get("agents.overrides", {})
        if overrides:
            for name, override_path in overrides.items():
                from azext_prototype.agents.loader import load_yaml_agent, load_python_agent
                full_path = str(Path(project_dir) / override_path)
                if override_path.endswith((".yaml", ".yml")):
                    agent = load_yaml_agent(full_path)
                elif override_path.endswith(".py"):
                    agent = load_python_agent(full_path)
                else:
                    continue
                agent.name = name  # Force the name to match the override target
                registry.register_override(agent)

    return registry


def _build_mcp_manager(config, project_dir: str):
    """Build an MCPManager from config + custom handler directory.

    Returns None if no MCP servers are configured.
    """
    from azext_prototype.mcp.base import MCPHandlerConfig
    from azext_prototype.mcp.registry import MCPRegistry
    from azext_prototype.mcp.manager import MCPManager
    from azext_prototype.mcp.builtin import register_all_builtin_mcp
    from azext_prototype.mcp.loader import load_handlers_from_directory

    mcp_config = config.get("mcp", {})
    server_configs = mcp_config.get("servers", [])

    if not server_configs:
        return None

    try:
        from azext_prototype.ui.console import console
    except Exception:
        console = None

    registry = MCPRegistry()

    # 1. Register built-in handlers
    register_all_builtin_mcp(registry)

    # 2. Build config map from prototype.yaml server entries
    configs: dict[str, MCPHandlerConfig] = {}
    for srv in server_configs:
        if not isinstance(srv, dict) or "name" not in srv:
            continue
        handler_config = MCPHandlerConfig(
            name=srv["name"],
            stages=srv.get("stages"),
            agents=srv.get("agents"),
            enabled=srv.get("enabled", True),
            timeout=srv.get("timeout", 30),
            max_retries=srv.get("max_retries", 2),
            max_result_bytes=srv.get("max_result_bytes", 8192),
            settings=srv.get("settings", {}),
        )
        configs[srv["name"]] = handler_config

    # 3. Load custom handlers from project directory
    custom_dir = mcp_config.get("custom_dir", ".prototype/mcp/")
    custom_path = str(Path(project_dir) / custom_dir)
    custom_handlers = load_handlers_from_directory(
        custom_path,
        configs,
        console=console,
        project_config=config.to_dict(),
    )
    for handler in custom_handlers:
        registry.register_custom(handler)

    if len(registry) == 0:
        return None

    return MCPManager(registry, console=console)


def _build_context(config, project_dir: str, mcp_manager=None):
    """Build an AgentContext for stage execution."""
    from azext_prototype.agents.base import AgentContext
    from azext_prototype.ai.factory import create_ai_provider

    ai_provider = create_ai_provider(config.to_dict())

    return AgentContext(
        project_config=config.to_dict(),
        project_dir=project_dir,
        ai_provider=ai_provider,
        mcp_manager=mcp_manager,
    )


def _check_requirements(iac_tool: str | None = None):
    """Run tool-version checks and raise CLIError on failures."""
    from azext_prototype.requirements import check_all

    results = check_all(iac_tool)
    problems = [r for r in results if r.status in ("fail", "missing")]
    if problems:
        lines = []
        for r in problems:
            line = f"  - {r.name}: {r.message}"
            if r.install_hint:
                line += f"\n    Install: {r.install_hint}"
            lines.append(line)
        raise CLIError("Tool requirements not met:\n" + "\n".join(lines))


def _prepare_command(project_dir: str | None = None):
    """Prepare the standard objects needed by most commands.

    Returns:
        (project_dir, config, registry, agent_context)
    """
    project_dir = project_dir or _get_project_dir()
    config = _load_config(project_dir)

    # Validate external tool versions before proceeding
    iac_tool = config.get("project.iac_tool")
    _check_requirements(iac_tool)

    registry = _build_registry(config, project_dir)
    mcp_manager = _build_mcp_manager(config, project_dir)
    agent_context = _build_context(config, project_dir, mcp_manager=mcp_manager)
    return project_dir, config, registry, agent_context


def _prepare_deploy_command(project_dir: str | None = None):
    """Prepare objects for deploy — AI provider is optional.

    Deploy is 100% subprocess-based (terraform/bicep/az CLI) and has zero
    runtime AI dependency.  The only AI touchpoint is ``route_error_to_qa()``,
    which already handles ``ai_provider is None`` gracefully.

    Returns:
        (project_dir, config, registry, agent_context)
    """
    from azext_prototype.agents.base import AgentContext

    project_dir = project_dir or _get_project_dir()
    config = _load_config(project_dir)

    # Validate external tool versions before proceeding
    iac_tool = config.get("project.iac_tool")
    _check_requirements(iac_tool)

    registry = _build_registry(config, project_dir)

    # Try to create AI provider for QA diagnosis, but don't fail if unavailable
    ai_provider = None
    try:
        from azext_prototype.ai.factory import create_ai_provider
        ai_provider = create_ai_provider(config.to_dict())
    except Exception:
        pass

    agent_context = AgentContext(
        project_config=config.to_dict(),
        project_dir=project_dir,
        ai_provider=ai_provider,
    )
    return project_dir, config, registry, agent_context


def _shutdown_mcp(agent_context):
    """Shut down MCP manager if present on the agent context."""
    if agent_context and getattr(agent_context, "mcp_manager", None):
        try:
            agent_context.mcp_manager.shutdown_all()
        except Exception:
            pass


def _check_guards(stage):
    """Check stage guards and raise CLIError on failure."""
    can_run, failures = stage.can_run()
    if not can_run:
        raise CLIError(
            "Prerequisites not met:\n" + "\n".join(f"  - {f}" for f in failures)
        )


def _get_registry_with_fallback(project_dir: str | None = None):
    """Load the registry, falling back to built-ins if no project config."""
    project_dir = project_dir or _get_project_dir()
    try:
        config = _load_config(project_dir)
        return _build_registry(config, project_dir)
    except CLIError:
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        registry = AgentRegistry()
        register_all_builtin(registry)
        return registry


# ======================================================================
# Stage Commands
# ======================================================================

@track("prototype init")
def prototype_init(
    cmd,
    name=None,
    location=None,
    iac_tool="terraform",
    ai_provider="copilot",
    output_dir=".",
    template=None,
    environment="dev",
    model=None,
):
    """Initialize a new prototype project."""
    from azext_prototype.stages.init_stage import InitStage
    from azext_prototype.agents.base import AgentContext
    from azext_prototype.agents.registry import AgentRegistry
    from azext_prototype.agents.builtin import register_all_builtin

    # Validate external tool versions before proceeding
    _check_requirements(iac_tool)

    stage = InitStage()

    # Check guards
    _check_guards(stage)

    # Init doesn't need a full context — create a minimal one
    registry = AgentRegistry()
    register_all_builtin(registry)

    # Placeholder context (no AI provider needed for init)
    context = AgentContext(
        project_config={},
        project_dir=str(Path(output_dir).resolve()),
        ai_provider=None,
    )

    result = stage.execute(
        context,
        registry,
        name=name,
        location=location,
        iac_tool=iac_tool,
        ai_provider=ai_provider,
        output_dir=output_dir,
        template=template,
        environment=environment,
        model=model,
    )

    # Attach resolved values so the @track decorator captures them
    # in telemetry (model may be None in kwargs — resolved inside stage).
    from azext_prototype.stages.init_stage import _DEFAULT_MODELS

    cmd._telemetry_overrides = {
        "location": location or "",
        "ai_provider": ai_provider,
        "model": model or _DEFAULT_MODELS.get(ai_provider, ""),
        "iac_tool": iac_tool,
        "environment": environment,
    }

    return result


@track("prototype design")
def prototype_design(cmd, artifacts=None, context=None, reset=False, interactive=False, status=False, skip_discovery=False):
    """Run the design stage.

    The design stage ALWAYS engages in an iterative discovery
    conversation with the user (via the biz-analyst agent), regardless
    of what flags are provided.

    When ``--context`` is supplied the agents will analyse the supplied
    text and ask targeted follow-up questions about anything that is
    unclear, missing, or ambiguous.  Agents NEVER assume.

    When ``--interactive`` is supplied the stage enters a refinement
    loop after the architect generates the initial design, allowing the
    user to review and iterate until they are satisfied.

    When ``--status`` is supplied, displays the current discovery state
    (confirmed items, open items, last session info) without starting
    a new session.

    Policy conflicts encountered during discovery are surfaced to the
    user, who may accept the compliant recommendation or override the
    policy.  Overrides are tracked in the design state.
    """
    from azext_prototype.stages.design_stage import DesignStage
    from azext_prototype.stages.discovery_state import DiscoveryState
    from azext_prototype.ui.console import console

    project_dir, config, registry, agent_context = _prepare_command()

    # Handle --status flag: just display current state and exit
    if status:
        discovery_state = DiscoveryState(project_dir)
        if discovery_state.exists:
            discovery_state.load()
            console.print_header("Discovery Status")
            console.print(f"Status: {discovery_state.format_status_summary()}")
            console.print()

            if discovery_state.open_count > 0:
                console.print(discovery_state.format_open_items())
                console.print()

            if discovery_state.confirmed_count > 0:
                console.print(discovery_state.format_confirmed_items())
                console.print()

            metadata = discovery_state.state.get("_metadata", {})
            if metadata.get("last_updated"):
                console.print_dim(f"Last updated: {metadata['last_updated']}")
            if metadata.get("exchange_count"):
                console.print_dim(f"Total exchanges: {metadata['exchange_count']}")
        else:
            console.print_warning("No discovery session found. Run 'az prototype design' to start.")

        return {"status": "displayed"}

    stage = DesignStage()
    _check_guards(stage)

    try:
        return stage.execute(
            agent_context,
            registry,
            artifacts=artifacts,
            context=context,
            reset=reset,
            interactive=interactive,
            skip_discovery=skip_discovery,
        )
    finally:
        _shutdown_mcp(agent_context)


@track("prototype build")
def prototype_build(cmd, scope="all", dry_run=False, status=False, reset=False, auto_accept=False):
    """Run the build stage.

    Interactive by default — uses Claude Code-inspired bordered prompts,
    staged code generation with policy enforcement, and a review loop.
    Use ``--dry-run`` for a non-interactive preview.
    """
    from azext_prototype.stages.build_stage import BuildStage
    from azext_prototype.stages.build_state import BuildState
    from azext_prototype.ui.console import console

    project_dir, _config, registry, agent_context = _prepare_command()

    # Handle --status flag (like design --status)
    if status:
        build_state = BuildState(project_dir)
        if build_state.exists:
            build_state.load()
            console.print_header("Build Status")
            console.print(build_state.format_stage_status())
        else:
            console.print_warning("No build session found. Run 'az prototype build' to start.")
        return {"status": "displayed"}

    stage = BuildStage()
    _check_guards(stage)

    try:
        result = stage.execute(
            agent_context,
            registry,
            scope=scope,
            dry_run=dry_run,
            reset=reset,
            auto_accept=auto_accept,
        )

        # Multi-resource telemetry
        if not dry_run and result.get("status") == "success":
            from azext_prototype.telemetry import track_build_resources

            track_build_resources(
                "prototype build",
                cmd=cmd,
                success=True,
                resources=result.get("resources", []),
                location=_config.get("project.location", ""),
                provider=_config.get("ai.provider", ""),
                model=_config.get("ai.model", ""),
            )

        return result
    finally:
        _shutdown_mcp(agent_context)


@track("prototype deploy")
def prototype_deploy(
    cmd,
    stage=None,
    force=False,
    dry_run=False,
    status=False,
    reset=False,
    tenant=None,
    service_principal=False,
    client_id=None,
    client_secret=None,
    tenant_id=None,
):
    """Run the deploy stage.

    Interactive by default — uses preflight checks, staged deployment with
    progress tracking, QA-first error routing, and a conversational loop
    with slash commands for rollback and redeployment.

    Use ``--status`` to view current deploy progress.
    Use ``--dry-run`` for what-if / terraform plan preview.
    Use ``--stage N`` for non-interactive single-stage deploy.
    Use ``--stage N --dry-run`` for what-if of a single stage.
    Use ``--service-principal`` with ``--client-id``, ``--client-secret``,
    and ``--tenant-id`` for cross-tenant service principal deployment.
    """
    from azext_prototype.stages.deploy_stage import DeployStage
    from azext_prototype.stages.deploy_state import DeployState
    from azext_prototype.ui.console import console

    # --subscription is an Azure CLI global parameter; extract from CLI context.
    # In Azure CLI, cmd.cli_ctx.data is a dict; in tests cmd is a MagicMock.
    subscription = None
    try:
        data = cmd.cli_ctx.data
        if isinstance(data, dict):
            subscription = data.get("subscription_id") or None
    except AttributeError:
        pass

    project_dir, _config, registry, agent_context = _prepare_deploy_command()

    # Handle --status flag (like design --status, build --status)
    if status:
        deploy_state = DeployState(project_dir)
        if deploy_state.exists:
            deploy_state.load()
            console.print_header("Deploy Status")
            console.print(deploy_state.format_stage_status())
        else:
            console.print_warning("No deploy session found. Run 'az prototype deploy' to start.")
        return {"status": "displayed"}

    # Service principal login (before guards — so az_logged_in guard passes)
    if service_principal:
        from azext_prototype.stages.deploy_helpers import login_service_principal

        sp_client_id = client_id or _config.get("deploy.service_principal.client_id")
        sp_secret = client_secret or _config.get("deploy.service_principal.client_secret")
        sp_tenant = tenant_id or tenant or _config.get("deploy.service_principal.tenant_id")

        if not all([sp_client_id, sp_secret, sp_tenant]):
            raise CLIError(
                "--service-principal requires client-id, client-secret, and tenant-id.\n"
                "Provide them via CLI flags or configure them:\n"
                "  az prototype config set --key deploy.service_principal.client_id --value <id>\n"
                "  az prototype config set --key deploy.service_principal.client_secret --value <secret>\n"
                "  az prototype config set --key deploy.service_principal.tenant_id --value <tenant>"
            )

        result = login_service_principal(sp_client_id, sp_secret, sp_tenant)
        if result["status"] == "failed":
            raise CLIError(f"Service principal login failed: {result['error']}")

        subscription = subscription or result.get("subscription")
        tenant = tenant or sp_tenant

    deploy_stage = DeployStage()
    _check_guards(deploy_stage)

    try:
        return deploy_stage.execute(
            agent_context,
            registry,
            stage=stage,
            force=force,
            dry_run=dry_run,
            reset=reset,
            subscription=subscription,
            tenant=tenant,
            client_id=sp_client_id if service_principal else None,
            client_secret=sp_secret if service_principal else None,
        )
    finally:
        _shutdown_mcp(agent_context)


@track("prototype deploy outputs")
def prototype_deploy_outputs(cmd):
    """Show captured deployment outputs.

    After infrastructure is deployed (Terraform / Bicep), the outputs
    are captured so that app deploy scripts can reference them.
    """
    from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    capture = DeploymentOutputCapture(project_dir)
    outputs = capture.get_all()

    console.print_header("Deployment Outputs")
    if not outputs:
        console.print_warning("No deployment outputs captured yet.")
        console.print_dim("Run 'az prototype deploy' first.")
        return {"status": "empty", "message": "No deployment outputs captured yet. Run 'az prototype deploy' first."}

    return outputs


@track("prototype deploy rollback-info")
def prototype_deploy_rollback_info(cmd):
    """Show rollback instructions based on deployment history."""
    from azext_prototype.stages.deploy_helpers import RollbackManager
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    mgr = RollbackManager(project_dir)
    snapshot = mgr.get_last_snapshot()
    instructions = mgr.get_rollback_instructions()

    console.print_header("Rollback Information")
    if not snapshot and not instructions:
        console.print_warning("No deployment history found.")
        console.print_dim("Run 'az prototype deploy' first.")

    return {
        "last_deployment": snapshot,
        "rollback_instructions": instructions,
    }


@track("prototype deploy generate-scripts")
def prototype_deploy_generate_scripts(
    cmd,
    scope="apps",
    deploy_type="webapp",
    resource_group=None,
    registry=None,
):
    """Generate deploy.sh scripts for application directories.

    Scans ./concept/apps/ for sub-directories and generates a deploy.sh
    in each one, tailored to the chosen deployment target.
    """
    from azext_prototype.stages.deploy_helpers import DeployScriptGenerator
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)
    apps_dir = Path(project_dir) / "concept" / "apps"

    if not apps_dir.is_dir():
        raise CLIError("No apps directory found. Run 'az prototype build --scope apps' first.")

    resource_group = resource_group or config.get("deploy.resource_group", "")
    registry = registry or ""
    generated = []

    console.print_header("Generate Deploy Scripts")
    console.print_info(f"Deploy type: {deploy_type}")

    for app_dir in sorted(apps_dir.iterdir()):
        if app_dir.is_dir() and not app_dir.name.startswith("."):
            DeployScriptGenerator.generate(
                app_dir=app_dir,
                app_name=app_dir.name,
                deploy_type=deploy_type,
                resource_group=resource_group,
                registry=registry,
            )
            generated.append(f"{app_dir.name}/deploy.sh")

    if not generated:
        console.print_warning("No application directories found.")
    else:
        console.print_file_list(generated)

    console.print_dim(f"{len(generated)} script(s) generated")
    return {"status": "generated", "scripts": generated, "deploy_type": deploy_type}


@track("prototype status")
def prototype_status(cmd, verbose=False, json_output=False):
    """Show current project status across all stages."""
    project_dir = _get_project_dir()

    try:
        config = _load_config(project_dir)
    except CLIError:
        if json_output:
            return {"status": "not_initialized", "message": "No prototype project found. Run 'az prototype init'."}
        from azext_prototype.ui.console import console
        console.print_warning("No prototype project found. Run 'az prototype init'.")
        return {"status": "not_initialized", "message": "No prototype project found. Run 'az prototype init'."}

    from azext_prototype.stages.discovery_state import DiscoveryState
    from azext_prototype.stages.build_state import BuildState
    from azext_prototype.stages.deploy_state import DeployState
    from azext_prototype.tracking import ChangeTracker

    stages_cfg = config.get("stages", {})
    project = config.get("project", {})

    # -- Build enriched status dict --

    status = {
        "project": project.get("name", "unknown"),
        "location": project.get("location", "unknown"),
        "environment": project.get("environment", "unknown"),
        "iac_tool": project.get("iac_tool", "unknown"),
        "ai_provider": config.get("ai.provider", "unknown"),
        "naming_strategy": config.get("naming.strategy", "unknown"),
        "project_id": project.get("id", ""),
        "stages": {},
    }

    # Design stage
    design_cfg = stages_cfg.get("design", {})
    design_info = {
        "completed": design_cfg.get("completed", False),
        "timestamp": design_cfg.get("timestamp"),
        "exchanges": 0,
        "confirmed": 0,
        "open": 0,
    }
    discovery_state = DiscoveryState(project_dir)
    if discovery_state.exists:
        discovery_state.load()
        meta = discovery_state.state.get("_metadata", {})
        design_info["exchanges"] = meta.get("exchange_count", 0)
        design_info["confirmed"] = discovery_state.confirmed_count
        design_info["open"] = discovery_state.open_count
    status["stages"]["design"] = design_info

    # Build stage
    build_cfg = stages_cfg.get("build", {})
    build_info = {
        "completed": build_cfg.get("completed", False),
        "timestamp": build_cfg.get("timestamp"),
        "templates_used": [],
        "total_stages": 0,
        "accepted_stages": 0,
        "files_generated": 0,
        "policy_overrides": 0,
    }
    build_state = BuildState(project_dir)
    if build_state.exists:
        build_state.load()
        bs = build_state.state
        build_info["templates_used"] = bs.get("templates_used", [])
        all_stages = bs.get("deployment_stages", [])
        build_info["total_stages"] = len(all_stages)
        build_info["accepted_stages"] = len([s for s in all_stages if s.get("status") == "accepted"])
        build_info["files_generated"] = len(bs.get("files_generated", []))
        build_info["policy_overrides"] = len(bs.get("policy_overrides", []))
    status["stages"]["build"] = build_info

    # Deploy stage
    deploy_cfg = stages_cfg.get("deploy", {})
    deploy_info = {
        "completed": deploy_cfg.get("completed", False),
        "timestamp": deploy_cfg.get("timestamp"),
        "total_stages": 0,
        "deployed": 0,
        "failed": 0,
        "rolled_back": 0,
        "outputs_captured": 0,
    }
    deploy_state = DeployState(project_dir)
    if deploy_state.exists:
        deploy_state.load()
        ds = deploy_state.state
        all_deploy_stages = ds.get("deployment_stages", [])
        deploy_info["total_stages"] = len(all_deploy_stages)
        deploy_info["deployed"] = len([s for s in all_deploy_stages if s.get("deploy_status") == "deployed"])
        deploy_info["failed"] = len([s for s in all_deploy_stages if s.get("deploy_status") == "failed"])
        deploy_info["rolled_back"] = len([s for s in all_deploy_stages if s.get("deploy_status") == "rolled_back"])
        outputs = ds.get("captured_outputs", {})
        deploy_info["outputs_captured"] = sum(len(v) for v in outputs.values() if isinstance(v, dict))
    status["stages"]["deploy"] = deploy_info

    # Pending changes
    tracker = ChangeTracker(project_dir)
    if build_cfg.get("completed") or deploy_state.exists:
        changes = tracker.get_changed_files("all")
        status["pending_changes"] = changes["total_changed"]

    # Deployment history
    status["deployment_history"] = tracker.get_deployment_history()

    # -- JSON mode: return enriched dict --
    if json_output:
        return status

    # -- Console display --
    from azext_prototype.ui.console import console

    name = status["project"]
    loc = status["location"]
    env = status["environment"]
    iac = status["iac_tool"]
    ai = status["ai_provider"]
    naming = status["naming_strategy"]

    console.print_header("Project Status")
    console.print(f"  Project: {name} ({loc}, {env})")
    console.print(f"  IaC: {iac} | AI: {ai} | Naming: {naming}")
    console.print()

    # Design line
    d = status["stages"]["design"]
    if discovery_state.exists or d["completed"]:
        if d["completed"]:
            icon, label = "v", "Complete"
        else:
            icon, label = "~", "In Progress"
        parts = []
        if d["exchanges"]:
            parts.append(f"{d['exchanges']} exchanges")
        parts.append(f"{d['confirmed']} confirmed")
        parts.append(f"{d['open']} open")
        console.print(f"  Design   [{icon}] {label} ({', '.join(parts)})")
    else:
        console.print("  Design   [-] Not started")

    # Build line
    b = status["stages"]["build"]
    if build_state.exists or b["completed"]:
        if b["completed"]:
            icon, label = "v", "Complete"
        else:
            icon, label = "~", "In Progress"
        parts = [f"{b['accepted_stages']}/{b['total_stages']} stages accepted"]
        parts.append(f"{b['files_generated']} files")
        if b["policy_overrides"]:
            parts.append(f"{b['policy_overrides']} policy override(s)")
        console.print(f"  Build    [{icon}] {label} ({', '.join(parts)})")
    else:
        console.print("  Build    [-] Not started")

    # Deploy line
    dp = status["stages"]["deploy"]
    if deploy_state.exists or dp["completed"]:
        if dp["completed"]:
            icon, label = "v", "Complete"
        else:
            icon, label = "~", "In Progress"
        parts = [f"{dp['deployed']}/{dp['total_stages']} deployed"]
        if dp["failed"]:
            parts.append(f"{dp['failed']} failed")
        if dp["rolled_back"]:
            parts.append(f"{dp['rolled_back']} rolled back")
        console.print(f"  Deploy   [{icon}] {label} ({', '.join(parts)})")
    else:
        console.print("  Deploy   [-] Not started")

    # Pending changes
    if "pending_changes" in status:
        console.print()
        count = status["pending_changes"]
        if count > 0:
            console.print_warning(f"  {count} file(s) changed since last deployment")
        else:
            console.print_dim("  No pending changes")

    # -- Verbose mode: expanded per-stage detail --
    if verbose:
        console.print()

        if discovery_state.exists:
            console.print_header("Design Detail")
            console.print(f"  {discovery_state.format_status_summary()}")
            if discovery_state.open_count > 0:
                console.print()
                console.print(discovery_state.format_open_items())
            if discovery_state.confirmed_count > 0:
                console.print()
                console.print(discovery_state.format_confirmed_items())
            meta = discovery_state.state.get("_metadata", {})
            if meta.get("last_updated"):
                console.print_dim(f"  Last updated: {meta['last_updated'][:19]}")
            console.print()

        if build_state.exists:
            console.print_header("Build Detail")
            console.print(build_state.format_stage_status())
            console.print()

        if deploy_state.exists:
            console.print_header("Deploy Detail")
            console.print(deploy_state.format_stage_status())
            console.print()

        history = status.get("deployment_history", [])
        if history:
            console.print_header("Deployment History")
            for entry in history[-5:]:  # last 5 deployments
                ts = entry.get("timestamp", "?")[:19]
                scope = entry.get("scope", "?")
                files = entry.get("files_count", 0)
                console.print(f"  {ts}  scope={scope}  files={files}")
            console.print()

    return {"status": "displayed"}


# ======================================================================
# Config Commands
# ======================================================================

@track("prototype config show")
def prototype_config_show(cmd):
    """Display current configuration.

    Secret values (API keys, subscription IDs, tokens) stored in
    ``prototype.secrets.yaml`` are masked as ``***`` in the output.
    """
    from azext_prototype.config import ProjectConfig, SECRET_KEY_PREFIXES

    config = _load_config()
    result = config.to_dict()

    # Mask secret values so they aren't leaked via CLI output
    for prefix in SECRET_KEY_PREFIXES:
        parts = prefix.split(".")
        node = result
        for part in parts[:-1]:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                break
        else:
            leaf = parts[-1]
            if isinstance(node, dict) and leaf in node and node[leaf]:
                node[leaf] = "***"

    return result


@track("prototype config get")
def prototype_config_get(cmd, key=None):
    """Get a single configuration value by dot-separated key."""
    from azext_prototype.config import ProjectConfig

    if not key:
        raise CLIError("--key is required.")

    config = _load_config()
    value = config.get(key)
    if value is None:
        raise CLIError(f"Key '{key}' not found in configuration.")

    # Mask secret values
    if ProjectConfig._is_secret_key(key) and value:
        return {"key": key, "value": "***"}

    return {"key": key, "value": value}


@track("prototype config set")
def prototype_config_set(cmd, key=None, value=None):
    """Set a configuration value."""
    if not key:
        raise CLIError("--key is required.")
    if value is None:
        raise CLIError("--value is required.")

    config = _load_config()

    # Try to parse value as JSON for structured values
    try:
        parsed = json.loads(value)
        config.set(key, parsed)
    except (json.JSONDecodeError, TypeError):
        config.set(key, value)

    return {"key": key, "value": config.get(key), "status": "updated"}


def _prompt_project_basics(console) -> tuple[str, str, str, str]:
    """Prompt for project name, location, environment, and IaC tool.

    Returns:
        (name, location, environment, iac_tool)
    """
    console.print_header("Project Basics")

    name = input("Project name [my-prototype]: ").strip() or "my-prototype"
    location = input("Azure region [eastus]: ").strip() or "eastus"
    environment = input("Environment (dev/staging/prod) [dev]: ").strip() or "dev"

    iac_tool = ""
    while iac_tool not in ("terraform", "bicep"):
        iac_tool = input("IaC tool (terraform/bicep) [terraform]: ").strip().lower() or "terraform"
        if iac_tool not in ("terraform", "bicep"):
            console.print_warning("Please choose 'terraform' or 'bicep'.")

    return name, location, environment, iac_tool


def _prompt_naming_config(console, name: str, location: str, environment: str) -> dict:
    """Prompt for resource naming strategy and return the naming config dict."""
    from azext_prototype.naming import get_available_strategies, ALZ_ZONE_IDS, create_naming_strategy

    console.print_header("Resource Naming Strategy")
    console.print_info("Available strategies:")
    console.print_dim("    1. microsoft-alz  — Azure Landing Zone (default)")
    console.print_dim("    2. microsoft-caf  — Cloud Adoption Framework")
    console.print_dim("    3. simple         — Quick prototypes")
    console.print_dim("    4. enterprise     — Business unit scoped")
    console.print_dim("    5. custom         — Define your own pattern")
    console.print()

    strategies = get_available_strategies()

    naming_choice = ""
    while naming_choice not in ("1", "2", "3", "4", "5"):
        naming_choice = input("Choose naming strategy (1-5) [1]: ").strip() or "1"
    naming_strategy = strategies[int(naming_choice) - 1]

    org = input(f"Organization/project short name [{name}]: ").strip() or name

    naming_config: dict = {
        "strategy": naming_strategy,
        "org": org,
        "env": environment,
    }

    if naming_strategy == "microsoft-alz":
        console.print()
        console.print_info("Landing Zone IDs:")
        for zid, zlabel in ALZ_ZONE_IDS.items():
            default_marker = " (default)" if zid == "zd" else ""
            console.print_dim(f"    {zid} — {zlabel}{default_marker}")
        zone_id = input("  Zone ID [zd]: ").strip().lower() or "zd"
        if zone_id not in ALZ_ZONE_IDS:
            console.print_warning(f"Unknown zone '{zone_id}', defaulting to 'zd'.")
            zone_id = "zd"
        naming_config["zone_id"] = zone_id

    if naming_strategy == "enterprise":
        bu = input("Business unit [eng]: ").strip() or "eng"
        naming_config["business_unit"] = bu

    if naming_strategy == "custom":
        console.print()
        console.print_info("Available placeholders: {org}, {env}, {region}, {region_short},")
        console.print_dim("  {service}, {type}, {suffix}, {instance}, {zoneid}")
        pattern = input("  Naming pattern [{type}-{org}-{service}-{env}-{region_short}]: ").strip()
        naming_config["pattern"] = pattern or "{type}-{org}-{service}-{env}-{region_short}"

    # Show example names in a panel
    preview_config = {
        "project": {"name": name, "location": location, "environment": environment},
        "naming": naming_config,
    }
    try:
        strategy = create_naming_strategy(preview_config)
        examples = (
            f"  Resource Group:    {strategy.resolve('resource_group', 'api')}\n"
            f"  Storage Account:   {strategy.resolve('storage_account', 'api')}\n"
            f"  App Service:       {strategy.resolve('app_service', 'api')}\n"
            f"  Key Vault:         {strategy.resolve('key_vault', 'api')}"
        )
        console.panel(examples, title=f"Example Names ({naming_strategy})")
    except Exception:
        pass

    return naming_config


def _prompt_ai_config(console) -> dict:
    """Prompt for AI provider configuration.

    Returns:
        dict suitable for the ``ai`` section of project config.
    """
    console.print_header("AI Provider")

    ai_provider = ""
    valid_providers = ("copilot", "github-models", "azure-openai")
    while ai_provider not in valid_providers:
        ai_provider = (
            input("AI provider (copilot/github-models/azure-openai) [copilot]: ").strip().lower()
            or "copilot"
        )
        if ai_provider not in valid_providers:
            console.print_warning("Please choose 'copilot', 'github-models', or 'azure-openai'.")

    default_model = "claude-sonnet-4.5" if ai_provider == "copilot" else "gpt-4o"
    model = input(f"AI model [{default_model}]: ").strip() or default_model

    ai_config: dict = {
        "provider": ai_provider,
        "model": model,
    }

    if ai_provider == "azure-openai":
        aoai_endpoint = input("Azure OpenAI endpoint (https://<name>.openai.azure.com/): ").strip()
        aoai_deployment = input(f"Azure OpenAI deployment name [{model}]: ").strip() or model
        if aoai_endpoint:
            ai_config["azure_openai"] = {
                "endpoint": aoai_endpoint,
                "deployment": aoai_deployment,
            }

    return ai_config


def _prompt_deploy_config(console) -> dict:
    """Prompt for optional deployment settings (subscription, resource group).

    Returns:
        dict suitable for the ``deploy`` section, or empty dict if nothing provided.
    """
    console.print_header("Deploy Targets (optional)")

    subscription = input("Azure subscription ID (optional): ").strip()
    resource_group = input("Resource group name (optional): ").strip()

    deploy: dict = {}
    if subscription:
        deploy["subscription"] = subscription
    if resource_group:
        deploy["resource_group"] = resource_group
    return deploy


def _prompt_backlog_config(current_provider: str = "", current_org: str = "", current_project: str = "") -> dict:
    """Prompt for backlog provider, org, and project when not configured.

    Only prompts for fields that are empty.  Returns a dict with all three
    keys populated (existing values are preserved).
    """
    from azext_prototype.ui.console import console

    result: dict = {}

    if not current_provider:
        console.print_header("Backlog Configuration")
        console.print_info("Choose your backlog provider:")
        console.print_dim("    1) github  — GitHub Issues with task checklists")
        console.print_dim("    2) devops  — Azure DevOps User Stories with Tasks")
        while True:
            choice = input("  Provider (1/2) [1]: ").strip() or "1"
            if choice in ("1", "github"):
                result["provider"] = "github"
                break
            if choice in ("2", "devops"):
                result["provider"] = "devops"
                break
            console.print_warning("Please enter 1 or 2.")
    else:
        result["provider"] = current_provider

    provider = result["provider"]

    if not current_org:
        if provider == "github":
            result["org"] = input("  GitHub owner or organization: ").strip()
        else:
            result["org"] = input("  Azure DevOps organization: ").strip()
    else:
        result["org"] = current_org

    if not current_project:
        if provider == "github":
            result["project"] = input("  GitHub repository name: ").strip()
        else:
            result["project"] = input("  Azure DevOps project name: ").strip()
    else:
        result["project"] = current_project

    return result


@track("prototype config init")
def prototype_config_init(cmd):
    """Interactive questionnaire to create prototype.yaml.

    Walks the user through standard project configuration questions.
    The config file is NOT required — but if the user chooses to create
    one here, subsequent commands can read defaults from it.
    """
    from datetime import datetime, timezone
    from azext_prototype.config import ProjectConfig
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = ProjectConfig(project_dir)

    if config.exists():
        console.print_warning("prototype.yaml already exists in this directory.")
        overwrite = input("Overwrite? [y/N] ").strip().lower()
        if overwrite != "y":
            return {"status": "cancelled", "message": "Existing configuration preserved."}

    console.panel(
        "Answer the following questions to create your\n"
        "prototype.yaml. Press Enter to accept defaults.",
        title="Prototype Configuration Setup",
    )

    name, location, environment, iac_tool = _prompt_project_basics(console)
    naming_config = _prompt_naming_config(console, name, location, environment)
    ai_config = _prompt_ai_config(console)
    deploy_config = _prompt_deploy_config(console)

    # Build the config overrides
    overrides: dict = {
        "project": {
            "name": name,
            "location": location,
            "environment": environment,
            "iac_tool": iac_tool,
        },
        "naming": naming_config,
        "ai": ai_config,
    }

    if deploy_config:
        overrides["deploy"] = deploy_config

    config.create_default(overrides)

    # Mark init as completed so downstream guards pass
    config.set("stages.init.completed", True)
    config.set("stages.init.timestamp", datetime.now(timezone.utc).isoformat())

    # Attach chosen values so the @track decorator can include them
    # in telemetry (config init has no kwargs of its own).
    cmd._telemetry_overrides = {
        "location": location,
        "ai_provider": ai_config.get("provider", ""),
        "model": ai_config.get("model", ""),
        "iac_tool": iac_tool,
        "environment": environment,
        "naming_strategy": naming_config.get("strategy", ""),
    }

    console.print_success("Configuration saved")
    created = ["prototype.yaml"]
    if config.secrets_path.exists():
        created.append("prototype.secrets.yaml (git-ignored)")
    console.print_file_list(created)
    console.print_dim("  You can edit it directly or use 'az prototype config set'.")

    result: dict = {"status": "created", "file": str(config.config_path)}
    if config.secrets_path.exists():
        result["secrets_file"] = str(config.secrets_path)
    return result


# ======================================================================
# Agent Commands
# ======================================================================

@track("prototype agent list")
def prototype_agent_list(cmd, show_builtin=True, verbose=False, json_output=False):
    """List all available agents."""
    registry = _get_registry_with_fallback()

    agents = registry.list_all_detailed()

    if not show_builtin:
        agents = [a for a in agents if a.get("source") != "builtin"]

    if json_output:
        return agents

    from azext_prototype.ui.console import console

    console.print_header("Agents")

    # Group by source
    groups: dict[str, list[dict]] = {"builtin": [], "custom": [], "override": []}
    for a in agents:
        groups.setdefault(a.get("source", "builtin"), []).append(a)

    for source_label, source_key in [("Built-in", "builtin"), ("Custom", "custom"), ("Override", "override")]:
        group = groups.get(source_key, [])
        if not group:
            continue
        console.print(f"  [accent]{source_label}[/accent]")
        for a in group:
            caps = ", ".join(a.get("capabilities", []))
            desc = a.get("description", "")
            if verbose:
                console.print(f"    {a['name']}")
                if desc:
                    console.print_dim(f"      {desc}")
                if caps:
                    console.print_dim(f"      Capabilities: {caps}")
            else:
                short_desc = (desc[:60] + "...") if len(desc) > 63 else desc
                line = f"    {a['name']}"
                if short_desc:
                    line += f"  — {short_desc}"
                console.print(line)
        console.print()

    total = len(agents)
    builtin_count = len(groups.get("builtin", []))
    custom_count = len(groups.get("custom", []))
    override_count = len(groups.get("override", []))
    parts = [f"{total} agent(s)"]
    if custom_count:
        parts.append(f"{custom_count} custom")
    if override_count:
        parts.append(f"{override_count} override(s)")
    console.print_dim(f"  {' · '.join(parts)}")

    return agents


@track("prototype agent add")
def prototype_agent_add(cmd, name=None, file=None, definition=None):
    """Add a custom agent.

    Interactive by default when neither ``--file`` nor ``--definition`` is
    provided.  Walks through description, capabilities, constraints, and
    system prompt.

    Non-interactive modes:
    - ``--definition cloud_architect`` — copy built-in definition
    - ``--file ./custom.yaml`` — use supplied YAML/Python file
    """
    if not name:
        raise CLIError("--name is required. Provide a unique name for the custom agent.")

    if file and definition:
        raise CLIError(
            "--file and --definition are mutually exclusive. "
            "Use --file to provide your own definition, or "
            "--definition to start from a built-in template."
        )

    import shutil
    from datetime import datetime, timezone
    from azext_prototype.agents.loader import load_yaml_agent, load_python_agent
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)
    custom_dir = Path(project_dir) / config.get("agents.custom_dir", ".prototype/agents/")
    custom_dir.mkdir(parents=True, exist_ok=True)

    # Determine the source file and destination filename
    definitions_dir = Path(__file__).parent / "agents" / "builtin" / "definitions"

    console.print_header("Add Agent")

    if file:
        # Mode 3: user-supplied file
        source_path = Path(file).resolve()
        if not source_path.exists():
            raise CLIError(f"File not found: {file}")
        dest_name = f"{name}{source_path.suffix}"
    elif definition:
        # Mode 2: copy a built-in definition
        source_path = _resolve_definition(definitions_dir, definition)
        dest_name = f"{name}.yaml"
    else:
        # Interactive mode — walk the user through agent creation
        agent_def = _prompt_agent_definition(console, name)
        dest_name = f"{name}.yaml"
        dest = custom_dir / dest_name

        if dest.exists():
            raise CLIError(
                f"Agent file already exists: {dest}\n"
                "Remove it first with 'az prototype agent remove' or choose a different name."
            )

        import yaml as _yaml
        dest.write_text(_yaml.dump(agent_def, default_flow_style=False, sort_keys=False), encoding="utf-8")

        agent = load_yaml_agent(str(dest))
        agent.name = name

        # Record in config manifest
        custom_manifest = config.get("agents.custom", {}) or {}
        relative_dest = str(Path(config.get("agents.custom_dir", ".prototype/agents/")) / dest_name)
        entry: dict = {
            "file": relative_dest,
            "description": agent.description,
            "capabilities": [c.value for c in agent.capabilities],
            "added": datetime.now(timezone.utc).isoformat(),
        }
        custom_manifest[name] = entry
        config.set("agents.custom", custom_manifest)

        console.print_success(f"Agent '{name}' created")
        console.print_file_list([relative_dest])
        console.print_dim("  Test it with: az prototype agent test --name " + name)

        return {
            "name": name,
            "description": agent.description,
            "file": str(dest),
            "based_on": None,
            "status": "added",
        }

    dest = custom_dir / dest_name

    if dest.exists():
        raise CLIError(
            f"Agent file already exists: {dest}\n"
            "Remove it first with 'az prototype agent remove' or choose a different name."
        )

    # Copy and (for YAML sources) rewrite the name field
    if str(source_path).endswith((".yaml", ".yml")) and not file:
        _copy_yaml_with_name(source_path, dest, name)
    else:
        shutil.copy2(str(source_path), str(dest))

    # Load the resulting agent to get metadata for the manifest
    if str(dest).endswith((".yaml", ".yml")):
        agent = load_yaml_agent(str(dest))
    elif str(dest).endswith(".py"):
        agent = load_python_agent(str(dest))
    else:
        raise CLIError("Agent file must be .yaml, .yml, or .py")

    agent.name = name  # ensure consistent naming

    # Record in config manifest
    custom_manifest = config.get("agents.custom", {}) or {}
    relative_dest = str(Path(config.get("agents.custom_dir", ".prototype/agents/")) / dest_name)
    entry = {
        "file": relative_dest,
        "description": agent.description,
        "capabilities": [c.value for c in agent.capabilities],
        "added": datetime.now(timezone.utc).isoformat(),
    }
    if definition:
        entry["based_on"] = definition
    custom_manifest[name] = entry
    config.set("agents.custom", custom_manifest)

    console.print_success(f"Agent '{name}' created")
    console.print_file_list([relative_dest])
    console.print_dim("  Test it with: az prototype agent test --name " + name)

    return {
        "name": name,
        "description": agent.description,
        "file": str(dest),
        "based_on": definition or ("example_custom_agent" if not file else None),
        "status": "added",
    }


def _prompt_agent_definition(console, name: str, existing: dict | None = None) -> dict:
    """Walk the user through defining an agent interactively.

    When *existing* is provided, values are used as defaults (for ``agent update``).

    Returns a dict suitable for YAML serialization.
    """
    from azext_prototype.agents.base import AgentCapability

    defaults = existing or {}

    # 1. Description
    default_desc = defaults.get("description", "")
    prompt_desc = f"Description [{default_desc}]: " if default_desc else "Description: "
    description = input(prompt_desc).strip() or default_desc

    # 2. Role
    default_role = defaults.get("role", "general")
    role = input(f"Role [{default_role}]: ").strip() or default_role

    # 3. Capabilities
    valid_caps = [c.value for c in AgentCapability]
    console.print_info(f"Valid capabilities: {', '.join(valid_caps)}")
    default_caps = ", ".join(defaults.get("capabilities", []))
    caps_prompt = f"Capabilities (comma-separated) [{default_caps}]: " if default_caps else "Capabilities (comma-separated): "
    caps_input = input(caps_prompt).strip() or default_caps
    capabilities = []
    if caps_input:
        for cap in caps_input.split(","):
            cap = cap.strip()
            if cap in valid_caps:
                capabilities.append(cap)
            elif cap:
                console.print_warning(f"Unknown capability '{cap}', skipping.")

    # 4. Constraints
    console.print_info("Constraints (one per line, empty line to finish):")
    default_constraints = defaults.get("constraints", [])
    if default_constraints:
        console.print_dim(f"  Current: {'; '.join(default_constraints)}")
    constraints: list[str] = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        constraints.append(line)
    if not constraints and default_constraints:
        constraints = default_constraints

    # 5. System prompt
    default_prompt = defaults.get("system_prompt", "")
    if default_prompt:
        preview = default_prompt[:80] + "..." if len(default_prompt) > 80 else default_prompt
        console.print_dim(f"  Current prompt: {preview}")
    console.print_info("System prompt (type END on its own line to finish, or press Enter to keep existing):")
    system_prompt = _read_multiline_input()
    if not system_prompt and default_prompt:
        system_prompt = default_prompt

    # 6. Examples (optional)
    console.print_info("Few-shot examples (optional, press Enter to skip):")
    default_examples = defaults.get("examples", [])
    examples: list[dict] = []
    while True:
        user_ex = input("  User example (or Enter to finish): ").strip()
        if not user_ex:
            break
        assistant_ex = input("  Assistant response: ").strip()
        if assistant_ex:
            examples.append({"user": user_ex, "assistant": assistant_ex})
    if not examples and default_examples:
        examples = default_examples

    agent_def: dict = {
        "name": name,
        "description": description,
        "role": role,
        "capabilities": capabilities,
    }
    if constraints:
        agent_def["constraints"] = constraints
    agent_def["system_prompt"] = system_prompt
    if examples:
        agent_def["examples"] = examples

    return agent_def


def _read_multiline_input() -> str:
    """Read multi-line input until the user types END on its own line."""
    lines: list[str] = []
    while True:
        line = input("  | ")
        if line.strip().upper() == "END":
            break
        if not lines and not line.strip():
            # First empty line means "keep existing"
            return ""
        lines.append(line)
    return "\n".join(lines)


def _resolve_definition(definitions_dir: Path, definition: str) -> Path:
    """Resolve a ``--definition`` value to an actual YAML file path."""
    # Accept with or without the .yaml extension
    stem = definition.removesuffix(".yaml").removesuffix(".yml")
    candidates = [
        definitions_dir / f"{stem}.yaml",
        definitions_dir / f"{stem}.yml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    available = sorted(
        p.stem for p in definitions_dir.glob("*.yaml") if p.stem != "example_custom_agent"
    )
    raise CLIError(
        f"Unknown definition '{definition}'. Available definitions:\n"
        + "\n".join(f"  - {d}" for d in available)
    )


def _copy_yaml_with_name(source: Path, dest: Path, new_name: str) -> None:
    """Copy a YAML definition file, rewriting the ``name:`` field."""
    import re

    content = source.read_text(encoding="utf-8")
    # Replace the first top-level 'name:' value
    content = re.sub(r"^(name:\s*).*$", rf"\g<1>{new_name}", content, count=1, flags=re.MULTILINE)
    dest.write_text(content, encoding="utf-8")


@track("prototype agent override")
def prototype_agent_override(cmd, name=None, file=None):
    """Override a built-in agent with validation."""
    if not name:
        raise CLIError("--name is required. Specify which built-in agent to override.")
    if not file:
        raise CLIError("--file is required. Provide a YAML or Python agent definition.")

    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)

    console.print_header("Override Agent")

    # Validate override file exists
    file_path = Path(project_dir) / file if not Path(file).is_absolute() else Path(file)
    if not file_path.exists():
        raise CLIError(f"Override file not found: {file}")

    # Validate YAML parse and name field
    if str(file_path).endswith((".yaml", ".yml")):
        import yaml as _yaml
        try:
            content = _yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except _yaml.YAMLError as e:
            raise CLIError(f"Invalid YAML in override file: {e}")
        if not isinstance(content, dict) or not content.get("name"):
            raise CLIError("Override YAML must contain a 'name' field.")

    # Warn if target is not a known built-in
    registry = _get_registry_with_fallback(project_dir)
    builtin_names = [a.name for a in registry.list_all() if a.is_builtin]
    if name not in builtin_names:
        console.print_warning(
            f"'{name}' is not a known built-in agent. "
            f"Available: {', '.join(builtin_names)}"
        )

    # Store override in config
    overrides = config.get("agents.overrides", {}) or {}
    overrides[name] = file
    config.set("agents.overrides", overrides)

    console.print_success(f"Override registered for '{name}'")
    console.print_dim(f"  File: {file}")
    console.print_dim(f"  Takes effect on next command run.")

    return {
        "name": name,
        "override_file": file,
        "status": "override_registered",
    }


@track("prototype agent show")
def prototype_agent_show(cmd, name=None, verbose=False, json_output=False):
    """Show agent details."""
    if not name:
        raise CLIError("--name is required.")

    registry = _get_registry_with_fallback()

    agent = registry.get(name)
    info = agent.to_dict()

    if verbose:
        info["system_prompt"] = agent.system_prompt
    else:
        info["system_prompt_preview"] = (
            agent.system_prompt[:200] + "..." if len(agent.system_prompt) > 200 else agent.system_prompt
        )

    if json_output:
        return info

    from azext_prototype.ui.console import console

    console.print_header(f"Agent: {name}")
    console.print(f"  Description: {info.get('description', '')}")
    source = "built-in" if info.get("is_builtin") else "custom"
    console.print(f"  Source: {source}")
    caps = ", ".join(info.get("capabilities", []))
    if caps:
        console.print(f"  Capabilities: {caps}")
    constraints = info.get("constraints", [])
    if constraints:
        console.print(f"  Constraints:")
        for c in constraints:
            console.print_dim(f"    - {c}")

    if verbose:
        console.print()
        console.print_info("System Prompt:")
        console.print(agent.system_prompt)
    else:
        prompt_preview = info.get("system_prompt_preview", "")
        if prompt_preview:
            console.print()
            console.print_dim(f"  Prompt: {prompt_preview}")

    return info


@track("prototype agent remove")
def prototype_agent_remove(cmd, name=None):
    """Remove a custom agent."""
    if not name:
        raise CLIError("--name is required.")

    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)
    registry = _build_registry(config, project_dir)

    console.print_header("Remove Agent")

    if registry.remove_custom(name):
        # Remove the file from the custom agents directory
        custom_dir = Path(project_dir) / config.get("agents.custom_dir", ".prototype/agents/")
        if custom_dir.is_dir():
            for f in custom_dir.iterdir():
                if f.stem == name or f.name.startswith(name):
                    f.unlink()
                    break

        # Remove from config manifest
        custom_manifest = config.get("agents.custom", {}) or {}
        if name in custom_manifest:
            del custom_manifest[name]
            config.set("agents.custom", custom_manifest)

        console.print_success(f"Agent '{name}' removed")
        return {"name": name, "status": "removed"}

    # Check if it's an override
    overrides = config.get("agents.overrides", {}) or {}
    if name in overrides:
        del overrides[name]
        config.set("agents.overrides", overrides)
        console.print_success(f"Override for '{name}' removed")
        return {"name": name, "status": "override_removed"}

    raise CLIError(f"Agent '{name}' is not a custom or override agent. Built-in agents cannot be removed.")


@track("prototype agent update")
def prototype_agent_update(cmd, name=None, description=None, capabilities=None, system_prompt_file=None):
    """Update an existing custom agent.

    Interactive by default — walks through the same prompts as ``agent add``
    with current values as defaults.  When any field flag is provided
    (``--description``, ``--capabilities``, ``--system-prompt-file``), the
    update is non-interactive and only the specified fields are changed.
    """
    if not name:
        raise CLIError("--name is required.")

    import yaml as _yaml
    from azext_prototype.agents.loader import load_yaml_agent
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)
    custom_dir = Path(project_dir) / config.get("agents.custom_dir", ".prototype/agents/")

    # Find the agent file
    agent_file = None
    for ext in (".yaml", ".yml"):
        candidate = custom_dir / f"{name}{ext}"
        if candidate.exists():
            agent_file = candidate
            break

    if agent_file is None:
        raise CLIError(
            f"Custom agent '{name}' not found in {custom_dir}.\n"
            "Only custom YAML agents can be updated."
        )

    # Load current definition
    current = _yaml.safe_load(agent_file.read_text(encoding="utf-8"))

    console.print_header("Update Agent")

    # Determine if interactive or targeted update
    has_field_flags = any(v is not None for v in [description, capabilities, system_prompt_file])

    if has_field_flags:
        # Non-interactive targeted update
        if description is not None:
            current["description"] = description
        if capabilities is not None:
            from azext_prototype.agents.base import AgentCapability
            valid_caps = {c.value for c in AgentCapability}
            parsed_caps = [c.strip() for c in capabilities.split(",") if c.strip()]
            for cap in parsed_caps:
                if cap not in valid_caps:
                    raise CLIError(f"Unknown capability '{cap}'. Valid: {', '.join(sorted(valid_caps))}")
            current["capabilities"] = parsed_caps
        if system_prompt_file is not None:
            prompt_path = Path(system_prompt_file)
            if not prompt_path.exists():
                raise CLIError(f"System prompt file not found: {system_prompt_file}")
            current["system_prompt"] = prompt_path.read_text(encoding="utf-8")
    else:
        # Interactive walkthrough with current values as defaults
        current = _prompt_agent_definition(console, name, existing=current)

    # Write updated YAML
    agent_file.write_text(_yaml.dump(current, default_flow_style=False, sort_keys=False), encoding="utf-8")

    # Reload and update config manifest
    agent = load_yaml_agent(str(agent_file))
    agent.name = name
    custom_manifest = config.get("agents.custom", {}) or {}
    if name in custom_manifest:
        custom_manifest[name]["description"] = agent.description
        custom_manifest[name]["capabilities"] = [c.value for c in agent.capabilities]
        config.set("agents.custom", custom_manifest)

    console.print_success(f"Agent '{name}' updated")

    return {
        "name": name,
        "description": agent.description,
        "capabilities": [c.value for c in agent.capabilities],
        "file": str(agent_file),
        "status": "updated",
    }


@track("prototype agent test")
def prototype_agent_test(cmd, name=None, prompt=None):
    """Send a test prompt to an agent and display the response.

    Requires a configured AI provider.
    """
    if not name:
        raise CLIError("--name is required.")

    from azext_prototype.ui.console import console

    project_dir, config, registry, agent_context = _prepare_command()
    agent = registry.get(name)

    test_prompt = prompt or "Briefly introduce yourself and describe your capabilities."

    console.print_header("Agent Test")
    console.print_info(f"Agent: {agent.name}")
    console.print_dim(f"  Prompt: {test_prompt}")

    with console.spinner(f"Waiting for {agent.name}..."):
        response = agent.execute(agent_context, test_prompt)

    console.print_agent_response(response.content)

    model = response.model or "unknown"
    tokens = response.usage or {}
    total_tokens = tokens.get("total_tokens", 0)
    console.print_dim(f"  Model: {model} · Tokens: {total_tokens}")

    return {
        "name": name,
        "model": model,
        "tokens": total_tokens,
        "status": "tested",
    }


@track("prototype agent export")
def prototype_agent_export(cmd, name=None, output=None):
    """Export any agent (including built-in) as a YAML file."""
    if not name:
        raise CLIError("--name is required.")

    import yaml as _yaml
    from azext_prototype.ui.console import console

    registry = _get_registry_with_fallback()
    agent = registry.get(name)

    # Build exportable dict
    export_data: dict = {
        "name": agent.name,
        "description": agent.description,
        "role": getattr(agent, "role", "general"),
        "capabilities": [c.value for c in agent.capabilities],
    }
    if agent.constraints:
        export_data["constraints"] = agent.constraints
    export_data["system_prompt"] = agent.system_prompt
    if hasattr(agent, "examples") and agent.examples:
        export_data["examples"] = agent.examples
    if hasattr(agent, "tools") and agent.tools:
        export_data["tools"] = agent.tools

    output_path = Path(output) if output else Path(f"./{name}.yaml")
    output_path.write_text(
        _yaml.dump(export_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    console.print_header("Export Agent")
    console.print_success(f"Agent '{name}' exported")
    console.print_file_list([str(output_path)])

    return {
        "name": name,
        "file": str(output_path),
        "status": "exported",
    }


# ======================================================================
# Analyze Commands — input-type handlers
# ======================================================================

def _analyze_image_input(qa_agent, agent_context, project_dir: str, image_path: Path):
    """Analyze an error from a screenshot/image using vision."""
    from azext_prototype.ui.console import console

    console.print_info(f"Analyzing screenshot: {image_path.name}")

    design_context = _load_design_context(project_dir)
    task = (
        "Analyze the error shown in this screenshot.\n\n"
        f"## Current Architecture\n{design_context}\n\n"
        "Identify the root cause and provide a fix with redeployment instructions."
    )
    return qa_agent.execute_with_image(agent_context, task, str(image_path))


def _analyze_file_input(qa_agent, agent_context, project_dir: str, file_path: Path):
    """Analyze an error from a log/text file."""
    from azext_prototype.ui.console import console

    console.print_info(f"Analyzing log file: {file_path.name}")

    file_content = file_path.read_text(encoding="utf-8", errors="replace")
    design_context = _load_design_context(project_dir)

    task = (
        "Analyze the following error log and identify the root cause.\n\n"
        f"## Current Architecture\n{design_context}\n\n"
        f"## Error Log ({file_path.name})\n```\n{file_content}\n```\n\n"
        "Provide a fix with redeployment instructions."
    )
    return qa_agent.execute(agent_context, task)


def _analyze_inline_input(qa_agent, agent_context, project_dir: str, error_text: str):
    """Analyze an inline error string."""
    from azext_prototype.ui.console import console

    console.print_info("Analyzing error...")

    design_context = _load_design_context(project_dir)
    task = (
        "Analyze the following error and identify the root cause.\n\n"
        f"## Current Architecture\n{design_context}\n\n"
        f"## Error\n```\n{error_text}\n```\n\n"
        "Provide a fix with redeployment instructions."
    )
    return qa_agent.execute(agent_context, task)


# ======================================================================
# Analyze Commands
# ======================================================================

@track("prototype analyze error")
def prototype_analyze_error(cmd, input=None):
    """Analyze an error and propose a fix.

    Accepts:
      - An inline error string
      - A path to a log file (.log, .txt, etc.)
      - A path to a screenshot image (.png, .jpg, .jpeg, .gif, .bmp, .webp)
    """
    if not input:
        raise CLIError(
            "Error input is required. Provide an inline error string, "
            "log file path, or screenshot path.\n"
            "  az prototype analyze error --input 'ResourceNotFound ...'\n"
            "  az prototype analyze error --input ./deploy.log\n"
            "  az prototype analyze error --input ./error-screenshot.png"
        )

    from azext_prototype.agents.base import AgentCapability
    from azext_prototype.ui.console import console

    project_dir, config, registry, agent_context = _prepare_command()
    qa_agents = registry.find_by_capability(AgentCapability.QA)
    if not qa_agents:
        raise CLIError("No QA agent available. The qa-engineer agent may have been removed.")

    qa_agent = qa_agents[0]

    console.print_header("Error Analysis")

    # Soft warning when no design context is available
    design_context = _load_design_context(project_dir)
    if not design_context:
        console.print_warning("No design context found. Analysis may be less accurate.")

    # Determine input type and dispatch
    input_path = Path(input)
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}

    if input_path.is_file() and input_path.suffix.lower() in image_extensions:
        response = _analyze_image_input(qa_agent, agent_context, project_dir, input_path)
    elif input_path.is_file():
        response = _analyze_file_input(qa_agent, agent_context, project_dir, input_path)
    else:
        response = _analyze_inline_input(qa_agent, agent_context, project_dir, input)

    console.print_agent_response(response.content)

    return {"status": "analyzed", "agent": qa_agent.name}


@track("prototype analyze costs")
def prototype_analyze_costs(cmd, output_format="markdown", refresh=False):
    """Analyze architecture costs at Small/Medium/Large t-shirt sizes.

    Results are cached in ``.prototype/state/cost_analysis.yaml``.
    Re-running returns cached results unless the design context changes.
    Use ``--refresh`` to force a fresh analysis.
    """
    import yaml as _yaml

    from azext_prototype.agents.base import AgentCapability
    from azext_prototype.ui.console import console

    project_dir, config, registry, agent_context = _prepare_command()

    # Find cost analyst agent
    cost_agents = registry.find_by_capability(AgentCapability.COST_ANALYSIS)
    if not cost_agents:
        raise CLIError("No cost analyst agent available.")

    cost_agent = cost_agents[0]

    # Load architecture
    design_context = _load_design_context(project_dir)
    if not design_context:
        raise CLIError("No architecture design found. Run 'az prototype design' first.")

    # Check cache (unless --refresh)
    cache_path = Path(project_dir) / ".prototype" / "state" / "cost_analysis.yaml"
    context_hash = hashlib.sha256(design_context.encode("utf-8")).hexdigest()[:16]

    if not refresh and cache_path.exists():
        try:
            cached = _yaml.safe_load(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("context_hash") == context_hash:
                console.print_header("Cost Analysis (cached)")
                console.print_dim("Using cached results. Run with --refresh to regenerate.")
                console.print_agent_response(cached["content"])
                return cached.get("result", {"status": "analyzed", "agent": cost_agent.name, "format": output_format})
        except Exception:
            pass  # Corrupted cache — fall through to fresh analysis

    console.print_header("Cost Analysis")
    console.print_info("Querying Azure Retail Prices API...")

    task = (
        "Analyze the costs for this Azure architecture at three t-shirt sizes.\n\n"
        f"## Architecture\n{design_context}"
    )

    response = cost_agent.execute(agent_context, task)

    console.print_agent_response(response.content)

    # Write to file
    if output_format == "markdown":
        report_path = Path(project_dir) / "concept" / "docs" / "COST_ESTIMATE.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(response.content, encoding="utf-8")
        console.print_success("Cost report saved to concept/docs/COST_ESTIMATE.md")

    # Save to cache
    result = {"status": "analyzed", "agent": cost_agent.name, "format": output_format}
    cache_data = {
        "context_hash": context_hash,
        "content": response.content,
        "result": result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(_yaml.dump(cache_data, default_flow_style=False), encoding="utf-8")

    return result


def _load_design_context(project_dir: str) -> str:
    """Load the current architecture design for context.

    Checks three sources in priority order:
    1. design.json — full architecture text from completed design stage
    2. discovery.yaml — structured learnings from discovery (even if
       the design stage has not fully completed)
    3. ARCHITECTURE.md — manually created or agent-generated docs
    """
    import json as _json

    # Source 1: design.json (canonical post-design architecture)
    design_path = Path(project_dir) / ".prototype" / "state" / "design.json"
    if design_path.exists():
        try:
            with open(design_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            arch = data.get("architecture", "")
            if arch:
                return arch
        except Exception:
            pass

    # Source 2: discovery.yaml (structured learnings)
    discovery_path = Path(project_dir) / ".prototype" / "state" / "discovery.yaml"
    if discovery_path.exists():
        try:
            from azext_prototype.stages.discovery_state import DiscoveryState

            ds = DiscoveryState(project_dir)
            ds.load()
            context = ds.format_as_context()
            if context:
                return context
        except Exception:
            pass

    # Source 3: ARCHITECTURE.md fallback
    arch_md = Path(project_dir) / "concept" / "docs" / "ARCHITECTURE.md"
    if arch_md.exists():
        return arch_md.read_text(encoding="utf-8", errors="replace")

    return ""


def _load_discovery_scope(project_dir: str) -> dict | None:
    """Load the scope dict from discovery state.

    Returns the scope dict with ``in_scope``, ``out_of_scope``, and
    ``deferred`` lists, or ``None`` if unavailable.
    """
    discovery_path = Path(project_dir) / ".prototype" / "state" / "discovery.yaml"
    if not discovery_path.exists():
        return None

    try:
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(project_dir)
        ds.load()
        scope = ds.state.get("scope", {})
        if scope and any(scope.get(k) for k in ("in_scope", "out_of_scope", "deferred")):
            return scope
    except Exception:
        pass

    return None


# ======================================================================
# Knowledge Commands
# ======================================================================

@track("prototype knowledge contribute")
def prototype_knowledge_contribute(
    cmd,
    service=None,
    description=None,
    file=None,
    draft=False,
    contribution_type="Pitfall",
    section=None,
):
    """Submit a knowledge base contribution as a GitHub Issue.

    Non-interactive when ``--service`` and ``--description`` (or ``--file``)
    are provided.  Use ``--draft`` to preview without submitting.
    """
    from azext_prototype.stages.knowledge_contributor import (
        format_contribution_body,
        format_contribution_title,
        submit_contribution,
    )
    from azext_prototype.ui.console import console

    console.print_header("Knowledge Contribution")

    # Build finding from file or arguments
    if file:
        file_path = Path(file)
        if not file_path.is_file():
            raise CLIError(f"File not found: {file}")
        content = file_path.read_text(encoding="utf-8", errors="replace")
        # Parse simple key: value lines from the file
        finding: dict = {
            "type": contribution_type,
            "source": f"File: {file_path.name}",
        }
        for line in content.split("\n"):
            if line.startswith("Service:"):
                finding["service"] = line.split(":", 1)[1].strip()
            elif line.startswith("Context:"):
                finding["context"] = line.split(":", 1)[1].strip()
            elif line.startswith("Content:"):
                finding["content"] = line.split(":", 1)[1].strip()
            elif line.startswith("Section:"):
                finding["section"] = line.split(":", 1)[1].strip()
            elif line.startswith("Type:"):
                finding["type"] = line.split(":", 1)[1].strip()
        # Use full file content as rationale/context fallback
        finding.setdefault("service", service or "unknown")
        finding.setdefault("context", content[:500])
        finding.setdefault("rationale", content[:500])
        finding.setdefault("content", content[:200])
    elif service and description:
        finding = {
            "service": service,
            "type": contribution_type,
            "file": f"knowledge/services/{service}.md",
            "section": section or "",
            "context": description,
            "rationale": description,
            "content": "",
            "source": "Manual CLI submission",
        }
    elif not service and not description and not file:
        # Interactive mode
        finding = _prompt_knowledge_contribution(contribution_type, section)
    else:
        raise CLIError(
            "Provide --service and --description together, or --file, "
            "or run without arguments for interactive mode."
        )

    title = format_contribution_title(finding)
    body = format_contribution_body(finding)

    if draft:
        console.print_info("Draft preview (not submitted):")
        console.print(f"  Title: {title}")
        console.print("")
        console.print(body)
        return {"status": "draft", "title": title, "body": body}

    # Submit
    from azext_prototype.stages.backlog_push import check_gh_auth

    if not check_gh_auth():
        raise CLIError(
            "gh CLI not authenticated. Run: gh auth login\n"
            "Use --draft to preview without submitting."
        )

    result = submit_contribution(finding)

    if result.get("error"):
        raise CLIError(f"Failed to submit: {result['error']}")

    console.print_success(f"Knowledge contribution submitted")
    console.print_dim(f"  {result.get('url', '')}")

    return {
        "status": "submitted",
        "url": result.get("url", ""),
        "number": result.get("number", ""),
        "title": title,
    }


def _prompt_knowledge_contribution(
    default_type: str = "Pitfall",
    default_section: str | None = None,
) -> dict:
    """Interactive walkthrough for knowledge contributions."""
    from azext_prototype.ui.console import console

    valid_types = [
        "Service pattern update", "New service",
        "Tool pattern", "Language pattern", "Pitfall",
    ]
    console.print_info("Types: " + ", ".join(f"{i+1}. {t}" for i, t in enumerate(valid_types)))
    type_choice = input(f"  Type [5]: ").strip() or "5"
    try:
        contribution_type = valid_types[int(type_choice) - 1]
    except (ValueError, IndexError):
        contribution_type = default_type

    service = input("  Service name (e.g., cosmos-db): ").strip()
    section = input(f"  Section [{default_section or ''}]: ").strip() or (default_section or "")

    console.print_info("Context (describe what was discovered):")
    context = input("  > ").strip()

    console.print_info("Rationale (why this matters):")
    rationale = input("  > ").strip() or context

    console.print_info("Content to add (type END on its own line to finish):")
    content_lines: list[str] = []
    while True:
        line = input("  | ")
        if line.strip().upper() == "END":
            break
        content_lines.append(line)
    content = "\n".join(content_lines)

    return {
        "service": service or "unknown",
        "type": contribution_type,
        "file": f"knowledge/services/{service}.md" if service else "",
        "section": section,
        "context": context,
        "rationale": rationale,
        "content": content,
        "source": "Manual CLI submission",
    }


# ======================================================================
# Generate Commands
# ======================================================================

# Template filename → output filename mapping
_DOC_TEMPLATES = {
    "ARCHITECTURE.md": "ARCHITECTURE.md",
    "DEPLOYMENT.md": "DEPLOYMENT.md",
    "DEVELOPMENT.md": "DEVELOPMENT.md",
    "CONFIGURATION.md": "CONFIGURATION.md",
    "AS_BUILT.md": "AS_BUILT.md",
    "COST_ESTIMATE.md": "COST_ESTIMATE.md",
}


def _get_templates_dir() -> Path:
    """Return the path to the bundled doc templates directory."""
    return Path(__file__).resolve().parent / "templates" / "docs"


def _render_template(template_text: str, project_config: dict) -> str:
    """Replace common placeholders in a template with project config values.

    Substitutes [PROJECT_NAME], [DATE], [LOCATION], and [CUSTOMER_NAME]
    from the project configuration.  All other [PLACEHOLDER] values are
    left intact for the AI agents to populate later.
    """
    from datetime import date

    replacements = {
        "[PROJECT_NAME]": project_config.get("project", {}).get("name", "[PROJECT_NAME]"),
        "[LOCATION]": project_config.get("project", {}).get("location", "[LOCATION]"),
        "[DATE]": str(date.today()),
        "[CUSTOMER_NAME]": project_config.get("project", {}).get("customer", "[CUSTOMER_NAME]"),
    }

    for placeholder, value in replacements.items():
        template_text = template_text.replace(placeholder, value)

    return template_text


def _generate_templates(
    output_dir: Path,
    project_dir: str,
    project_config: dict,
    label: str,
    include_manifest: bool = False,
    ai_provider=None,
    design_context: str = "",
    registry=None,
) -> list[str]:
    """Render doc templates into *output_dir*.

    Shared implementation for ``generate docs`` and ``generate speckit``.
    When *ai_provider* and *design_context* are available, uses the
    doc-agent to populate template placeholders with real content.
    Returns the list of generated file names.
    """
    from azext_prototype.ui.console import console

    output_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = _get_templates_dir()
    generated: list[str] = []

    # Resolve doc-agent if available
    doc_agent = None
    if ai_provider and design_context and registry:
        from azext_prototype.agents.base import AgentCapability
        doc_agents = registry.find_by_capability(AgentCapability.DOCUMENT)
        if doc_agents:
            doc_agent = doc_agents[0]

    for template_name in _DOC_TEMPLATES:
        template_path = templates_dir / template_name
        if not template_path.exists():
            logger.warning("Template not found: %s", template_name)
            continue

        template_text = template_path.read_text(encoding="utf-8")
        rendered = _render_template(template_text, project_config)

        # AI population: if doc-agent is available, fill remaining placeholders
        if doc_agent and "[PLACEHOLDER]" in rendered or (doc_agent and "[" in rendered):
            try:
                from azext_prototype.agents.base import AgentContext
                from azext_prototype.ai.provider import AIMessage

                with console.spinner(f"Populating {template_name}..."):
                    task = (
                        f"Populate this documentation template using the architecture below. "
                        f"Replace all [PLACEHOLDER] values with real content. "
                        f"Keep the same markdown structure.\n\n"
                        f"## Template\n```\n{rendered}\n```\n\n"
                        f"## Architecture\n{design_context}\n\n"
                        f"Return ONLY the populated template content."
                    )
                    ctx = AgentContext(
                        project_config=project_config,
                        project_dir=project_dir,
                        ai_provider=ai_provider,
                    )
                    response = doc_agent.execute(ctx, task)
                    if response and response.content:
                        rendered = response.content
            except Exception as e:
                logger.warning("AI population failed for %s: %s", template_name, e)

        output_name = _DOC_TEMPLATES.get(template_name, template_name)
        output_path = output_dir / output_name
        output_path.write_text(rendered, encoding="utf-8")
        generated.append(output_name)

    if include_manifest:
        from datetime import date as _date

        manifest = {
            "speckit_version": "1.0",
            "project": project_config.get("project", {}).get("name", "unknown"),
            "generated": str(_date.today()),
            "templates": generated,
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    console.print_file_list(generated)
    console.print_dim(f"  {len(generated)} file(s) generated in {os.path.relpath(output_dir, project_dir)}/")
    return generated


@track("prototype generate backlog")
def prototype_generate_backlog(
    cmd,
    provider=None,
    org=None,
    project=None,
    output_format="markdown",
    quick=False,
    refresh=False,
    status=False,
    push=False,
):
    """Generate a backlog of user stories / issues from the architecture design.

    Interactive by default — uses a conversational session where you can
    review, refine, and push backlog items. Use ``--quick`` for a lighter
    generate → confirm → push flow. Use ``--status`` to show current state.
    """
    from azext_prototype.stages.backlog_state import BacklogState
    from azext_prototype.ui.console import console

    project_dir, config, registry, agent_context = _prepare_command()

    # Handle --status flag
    if status:
        backlog_state = BacklogState(project_dir)
        if backlog_state.exists:
            backlog_state.load()
            console.print_header("Backlog Status")
            console.print(backlog_state.format_backlog_summary())
        else:
            console.print_warning("No backlog session found. Run 'az prototype generate backlog' to start.")
        return {"status": "displayed"}

    # Resolve provider settings: CLI args override config, prompt if still empty
    backlog_cfg = config.to_dict().get("backlog", {})
    provider = provider or backlog_cfg.get("provider", "")
    org = org or backlog_cfg.get("org", "")
    project_name = project or backlog_cfg.get("project", "")

    # If any required field is missing, prompt interactively
    if not provider or not org or not project_name:
        prompted = _prompt_backlog_config(provider, org, project_name)
        provider = prompted["provider"]
        org = prompted["org"]
        project_name = prompted["project"]

        # Save to prototype.yaml so user isn't asked again
        config.set("backlog.provider", provider)
        if org:
            config.set("backlog.org", org)
        if project_name:
            config.set("backlog.project", project_name)

    if provider not in ("github", "devops"):
        raise CLIError(
            f"Unsupported backlog provider '{provider}'. "
            "Supported values: github, devops"
        )

    # Load architecture
    design_context = _load_design_context(project_dir)
    if not design_context:
        raise CLIError("No architecture design found. Run 'az prototype design' first.")

    # Load scope from discovery
    scope = _load_discovery_scope(project_dir)

    # Delegate to BacklogSession
    from azext_prototype.stages.backlog_session import BacklogSession

    backlog_state = BacklogState(project_dir)
    if backlog_state.exists and not refresh:
        backlog_state.load()

    session = BacklogSession(
        agent_context,
        registry,
        console=console,
        backlog_state=backlog_state,
    )

    try:
        result = session.run(
            design_context=design_context,
            scope=scope,
            provider=provider,
            org=org,
            project=project_name,
            refresh=refresh,
            quick=quick or push,
        )
    finally:
        _shutdown_mcp(agent_context)

    # Telemetry overrides
    cmd._telemetry_overrides = {
        "backlog_provider": provider,
        "output_format": output_format,
        "items_pushed": result.items_pushed,
    }

    if result.cancelled:
        return {"status": "cancelled", "provider": provider}

    return {
        "status": "generated",
        "provider": provider,
        "format": output_format,
        "items_generated": result.items_generated,
        "items_pushed": result.items_pushed,
        "items_failed": result.items_failed,
        "push_urls": result.push_urls,
    }


@track("prototype generate docs")
def prototype_generate_docs(cmd, path=None):
    """Generate documentation from templates.

    When design context is available, uses the doc-agent to populate
    templates with real content. Falls back to static rendering if
    no design context or AI is unavailable.
    """
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)
    project_config = config.to_dict()

    output_dir = Path(path) if path else Path(project_dir) / "docs"

    console.print_header("Generating Documentation")

    # Try to get AI provider and design context for population
    ai_provider = None
    design_context = _load_design_context(project_dir)
    registry = None
    if design_context:
        try:
            from azext_prototype.ai.factory import create_ai_provider
            ai_provider = create_ai_provider(project_config)
            registry = _build_registry(config, project_dir)
            console.print_info("Design context available — populating templates with AI.")
        except Exception:
            console.print_dim("  AI unavailable — using static templates.")
    else:
        console.print_dim("  No design context — using static templates.")

    generated = _generate_templates(
        output_dir, project_dir, project_config, "docs",
        ai_provider=ai_provider,
        design_context=design_context,
        registry=registry,
    )
    console.print_success(f"Documentation generated to {os.path.relpath(output_dir, project_dir)}/")
    return {"status": "generated", "documents": generated, "output_dir": str(output_dir)}


@track("prototype generate speckit")
def prototype_generate_speckit(cmd, path=None):
    """Generate the spec-kit documentation bundle.

    When design context is available, uses the doc-agent to populate
    templates with real content. Falls back to static rendering if
    no design context or AI is unavailable.
    """
    from azext_prototype.ui.console import console

    project_dir = _get_project_dir()
    config = _load_config(project_dir)
    project_config = config.to_dict()

    output_dir = Path(path) if path else Path(project_dir) / "concept" / ".specify"

    console.print_header("Generating Spec-Kit")

    # Try to get AI provider and design context for population
    ai_provider = None
    design_context = _load_design_context(project_dir)
    registry = None
    if design_context:
        try:
            from azext_prototype.ai.factory import create_ai_provider
            ai_provider = create_ai_provider(project_config)
            registry = _build_registry(config, project_dir)
            console.print_info("Design context available — populating templates with AI.")
        except Exception:
            console.print_dim("  AI unavailable — using static templates.")
    else:
        console.print_dim("  No design context — using static templates.")

    generated = _generate_templates(
        output_dir, project_dir, project_config, "speckit",
        include_manifest=True,
        ai_provider=ai_provider,
        design_context=design_context,
        registry=registry,
    )
    console.print_success(f"Spec-kit generated to {os.path.relpath(output_dir, project_dir)}/")
    return {"status": "generated", "templates": generated, "output_dir": str(output_dir)}
