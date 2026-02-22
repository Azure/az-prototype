"""Deploy stage — orchestrate interactive or targeted deployments.

Delegates to :class:`~.deploy_session.DeploySession` for execution.  Supports:

- **Default (interactive)**: Full session with preflight, staged deploy, rollback, slash commands
- ``--status``: Show current deploy progress without starting a session
- ``--reset``: Clear deploy state and start fresh
- ``--dry-run``: What-if / terraform plan preview (all stages or ``--stage N``)
- ``--stage N``: Deploy only stage N (non-interactive)
"""

import logging
from pathlib import Path

from azext_prototype.agents.base import AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.stages.base import BaseStage, StageGuard, StageState
from azext_prototype.stages.deploy_helpers import check_az_login
from azext_prototype.stages.deploy_session import DeploySession
from azext_prototype.stages.deploy_state import DeployState
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)


class DeployStage(BaseStage):
    """Deploy prototype to Azure via interactive session.

    The stage itself is a thin orchestrator — all real work is done by
    :class:`DeploySession` (interactive loop), :class:`DeployState`
    (persistence), and the execution primitives in ``deploy_helpers``.
    """

    def __init__(self):
        super().__init__(
            name="deploy",
            description="Deploy to Azure with interactive session",
            reentrant=True,
        )

    def get_guards(self) -> list[StageGuard]:
        return [
            StageGuard(
                name="project_initialized",
                description="Project must be initialized",
                check_fn=lambda: Path("prototype.yaml").is_file(),
                error_message="Run 'az prototype init' first.",
            ),
            StageGuard(
                name="build_complete",
                description="Build stage must be completed",
                check_fn=lambda: Path(".prototype/state/build.yaml").is_file(),
                error_message="Run 'az prototype build' first.",
            ),
            StageGuard(
                name="az_logged_in",
                description="Must be logged into Azure CLI",
                check_fn=check_az_login,
                error_message="Run 'az login' first.",
            ),
        ]

    def execute(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        **kwargs,
    ) -> dict:
        """Execute the deploy stage, delegating to DeploySession.

        Routing:
        - ``--status``   → display stage status and return
        - ``--reset``    → clear deploy state and return
        - ``--dry-run``  → ``DeploySession.run_dry_run()``
        - ``--stage N``  → ``DeploySession.run_single_stage(N)``
        - Default        → ``DeploySession.run()`` (interactive)
        """
        target_stage = kwargs.get("stage")  # int | None
        force = kwargs.get("force", False)
        dry_run = kwargs.get("dry_run", False)
        status = kwargs.get("status", False)
        reset = kwargs.get("reset", False)
        subscription = kwargs.get("subscription")
        tenant = kwargs.get("tenant")
        client_id = kwargs.get("client_id")
        client_secret = kwargs.get("client_secret")

        self.state = StageState.IN_PROGRESS

        # --status: show current deploy progress
        if status:
            ds = DeployState(agent_context.project_dir)
            ds.load()
            default_console.print_info(ds.format_stage_status())
            self.state = StageState.COMPLETED
            return {"status": "status_displayed"}

        # --reset: clear deploy state
        if reset:
            ds = DeployState(agent_context.project_dir)
            ds.reset()
            default_console.print_success("Deploy state cleared.")
            self.state = StageState.COMPLETED
            return {"status": "reset"}

        # Create session
        session = DeploySession(agent_context, registry)

        # --dry-run (with optional --stage N)
        if dry_run:
            result = session.run_dry_run(
                target_stage=target_stage,
                subscription=subscription,
                tenant=tenant,
                client_id=client_id,
                client_secret=client_secret,
            )
            self.state = StageState.COMPLETED
            return _result_to_dict(result, "dry-run")

        # --stage N (non-interactive single-stage deploy)
        if target_stage is not None:
            result = session.run_single_stage(
                target_stage,
                subscription=subscription,
                tenant=tenant,
                force=force,
                client_id=client_id,
                client_secret=client_secret,
            )
            self.state = StageState.COMPLETED if not result.failed_stages else StageState.FAILED
            return _result_to_dict(result, "single_stage")

        # Default: interactive session
        result = session.run(
            subscription=subscription,
            tenant=tenant,
            force=force,
            client_id=client_id,
            client_secret=client_secret,
        )

        if result.cancelled:
            self.state = StageState.COMPLETED
            return {"status": "cancelled"}

        self.state = StageState.COMPLETED if not result.failed_stages else StageState.FAILED
        return _result_to_dict(result, "interactive")


def _result_to_dict(result, mode: str) -> dict:
    """Convert a DeployResult to a serializable dict."""
    status = "success"
    if result.failed_stages:
        status = "partial_failure"
    elif result.cancelled:
        status = "cancelled"

    return {
        "status": status,
        "mode": mode,
        "deployed": len(result.deployed_stages),
        "failed": len(result.failed_stages),
        "rolled_back": len(result.rolled_back_stages),
        "captured_outputs": result.captured_outputs,
    }
