"""GitHub authentication manager using gh CLI."""

import json
import subprocess
import logging

from knack.util import CLIError

logger = logging.getLogger(__name__)


class GitHubAuthManager:
    """Manages GitHub authentication via the gh CLI.

    Requires the GitHub CLI (gh) to be installed and handles the
    authentication flow for accessing GitHub Models API.
    """

    GH_CLI = "gh"

    def __init__(self):
        self._user_info: dict | None = None
        self._token: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_authenticated(self) -> dict:
        """Verify the user is authenticated with GitHub via gh CLI.

        Returns:
            dict with user info (login, name, email, etc.)

        Raises:
            CLIError if gh is not installed or user is not authenticated.
        """
        self._check_gh_installed()

        status = self._run_gh(["auth", "status", "--show-token"], check=False)

        if status.returncode != 0:
            logger.info("User not authenticated with GitHub. Initiating login...")
            self._initiate_login()

        self._user_info = self._get_user_info()
        logger.info("Authenticated as GitHub user: %s", self._user_info.get("login"))
        return self._user_info

    def get_token(self) -> str:
        """Retrieve the GitHub token for API calls.

        Returns:
            GitHub personal access token string.
        """
        if self._token:
            return self._token

        result = self._run_gh(["auth", "token"])
        self._token = result.stdout.strip()
        return self._token

    def get_user_info(self) -> dict:
        """Return cached user info or fetch it."""
        if self._user_info:
            return self._user_info
        return self._get_user_info()

    # ------------------------------------------------------------------
    # Repo operations (used by init stage)
    # ------------------------------------------------------------------

    def create_repo(self, name: str, private: bool = True, description: str = "") -> dict:
        """Create a GitHub repository.

        Args:
            name: Repository name.
            private: Whether the repo should be private.
            description: Repo description.

        Returns:
            dict with repo info (url, clone_url, etc.)
        """
        args = ["repo", "create", name, "--confirm"]
        if private:
            args.append("--private")
        if description:
            args.extend(["--description", description])

        self._run_gh(args)

        # Fetch repo details
        result = self._run_gh(["repo", "view", name, "--json",
                               "url,sshUrl,name,owner"])
        return json.loads(result.stdout)

    def clone_repo(self, repo: str, directory: str | None = None) -> str:
        """Clone a repository locally.

        Args:
            repo: Repository in owner/name format.
            directory: Target directory (optional).

        Returns:
            Path to cloned directory.
        """
        args = ["repo", "clone", repo]
        if directory:
            args.append(directory)

        self._run_gh(args)
        return directory or repo.split("/")[-1]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_gh_installed(self):
        """Verify gh CLI is available on PATH."""
        try:
            subprocess.run(
                [self.GH_CLI, "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise CLIError(
                "GitHub CLI (gh) is not installed. "
                "Install it from https://cli.github.com/ and run 'gh auth login'."
            )

    def _initiate_login(self):
        """Run interactive gh auth login."""
        logger.info("Starting GitHub authentication flow...")
        result = subprocess.run(
            [self.GH_CLI, "auth", "login", "--web", "--scopes", "read:user,repo,models:read"],
            check=False,
        )
        if result.returncode != 0:
            raise CLIError(
                "GitHub authentication failed. Run 'gh auth login' manually and retry."
            )

    def _get_user_info(self) -> dict:
        """Fetch authenticated user profile."""
        result_json = self._run_gh(["api", "user"])
        return json.loads(result_json.stdout)

    def _run_gh(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Execute a gh CLI command.

        Args:
            args: Arguments to pass to gh.
            check: Whether to raise on non-zero exit.

        Returns:
            CompletedProcess result.
        """
        cmd = [self.GH_CLI] + args
        logger.debug("Running: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if check and result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            raise CLIError(f"GitHub CLI error: {error_msg}")

        return result
