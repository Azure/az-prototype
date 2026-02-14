"""Copilot credential resolution.

Resolves a GitHub OAuth / PAT token from multiple sources for use
with the Copilot completions API at ``api.githubcopilot.com``.

The raw token (``gho_``, ``ghu_``, ``ghp_``, etc.) is sent directly
as a ``Bearer`` header to the completions API — no JWT exchange is
required.  Editor-identification headers are added by the provider.

Token resolution order (first wins):
1. ``COPILOT_GITHUB_TOKEN`` environment variable (highest priority)
2. ``GH_TOKEN`` environment variable
3. Copilot CLI keychain credential (system Credential Manager / Keychain)
4. Copilot SDK config files (``hosts.json`` / ``apps.json``)
5. ``gh auth token`` from the GitHub CLI
6. ``GITHUB_TOKEN`` environment variable
"""

import json
import logging
import os
import platform
import subprocess
from pathlib import Path

from knack.util import CLIError

logger = logging.getLogger(__name__)


# ======================================================================
# Copilot CLI config directory
# ======================================================================


def _get_copilot_cli_config_dir() -> Path:
    """Return the Copilot CLI config directory (``~/.copilot`` by default).

    Respects ``XDG_CONFIG_HOME`` / ``XDG_STATE_HOME`` on Linux,
    but the Copilot CLI always defaults to ``~/.copilot`` on all
    platforms unless those env vars are set.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".copilot"


def _get_copilot_config_dir() -> Path:
    """Return the platform-specific *legacy* Copilot config directory.

    This is the older ``github-copilot`` directory used by the
    VS Code Copilot extension (``hosts.json`` / ``apps.json``).
    """
    system = platform.system()

    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / "github-copilot"
        return Path.home() / "AppData" / "Local" / "github-copilot"

    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "github-copilot"

    # Linux / other
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "github-copilot"
    return Path.home() / ".config" / "github-copilot"


# ======================================================================
# Keychain / Credential Manager readers
# ======================================================================


def _read_keychain_token() -> str | None:
    """Read the OAuth token stored by ``copilot login`` in the OS keychain.

    On **Windows** the Copilot CLI writes to the Credential Manager
    under a target like ``copilot-cli/https://github.com:<login>``.
    The login name is read from ``~/.copilot/config.json``.

    On **macOS / Linux** the CLI may use the system keyring via
    the ``keyring`` command or ``secret-tool``.  This function
    currently only supports the Windows Credential Manager.

    Returns *None* if the credential cannot be read.
    """
    if platform.system() != "Windows":
        return None

    # Read logged-in user from Copilot CLI config
    config_path = _get_copilot_cli_config_dir() / "config.json"
    if not config_path.exists():
        return None

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to read Copilot CLI config: %s", exc)
        return None

    # Extract login(s) from config
    logins: list[str] = []
    last_user = cfg.get("last_logged_in_user", {})
    if isinstance(last_user, dict) and last_user.get("login"):
        logins.append(last_user["login"])
    for user in cfg.get("logged_in_users", []):
        if isinstance(user, dict) and user.get("login"):
            login = user["login"]
            if login not in logins:
                logins.append(login)

    if not logins:
        return None

    # Try to read from Windows Credential Manager via ctypes
    try:
        import ctypes
        import ctypes.wintypes

        advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]

        CRED_TYPE_GENERIC = 1

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", ctypes.wintypes.DWORD),
                ("Type", ctypes.wintypes.DWORD),
                ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p),
                ("LastWritten", ctypes.wintypes.FILETIME),
                ("CredentialBlobSize", ctypes.wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", ctypes.wintypes.DWORD),
                ("AttributeCount", ctypes.wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p),
            ]

        cred_ptr = ctypes.POINTER(CREDENTIAL)()

        for login in logins:
            host = last_user.get("host", "https://github.com").rstrip("/")
            target = f"copilot-cli/{host}:{login}"

            ok = advapi32.CredReadW(
                target,
                CRED_TYPE_GENERIC,
                0,
                ctypes.byref(cred_ptr),
            )
            if not ok:
                continue

            try:
                cred = cred_ptr.contents
                size = cred.CredentialBlobSize
                if size == 0:
                    continue
                blob = ctypes.string_at(cred.CredentialBlob, size)
                token = blob.decode("utf-8", errors="replace").strip()
                if token:
                    logger.debug(
                        "Found Copilot CLI token in Credential Manager for %s",
                        login,
                    )
                    return token
            finally:
                advapi32.CredFree(cred_ptr)

    except Exception as exc:  # noqa: BLE001  — ctypes errors are broad
        logger.debug("Failed to read Windows Credential Manager: %s", exc)

    return None


def _read_oauth_token() -> str | None:
    """Read the Copilot OAuth token from the legacy SDK config files.

    Checks ``hosts.json`` first (newer format), then ``apps.json``
    (older format).  Returns *None* if no token is found.
    """
    config_dir = _get_copilot_config_dir()

    for filename in ("hosts.json", "apps.json"):
        token_file = config_dir / filename
        if not token_file.exists():
            continue
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read %s: %s", token_file, exc)
            continue

        # Format: {"github.com": {"oauth_token": "ghu_..."}}
        for _host, host_data in data.items():
            if isinstance(host_data, dict):
                token = host_data.get("oauth_token")
                if token:
                    logger.debug("Found Copilot OAuth token in %s", filename)
                    return token

    return None


def _read_gh_token() -> str | None:
    """Read the GitHub token from the ``gh`` CLI (``gh auth token``).

    The ``gh`` CLI on Windows stores tokens in the Credential Manager,
    so they may not appear in ``hosts.yml``.  ``gh auth token`` always
    returns the active token regardless of storage backend.

    Returns *None* if ``gh`` is not installed or not authenticated.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.debug("Obtained token from gh CLI")
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


# ======================================================================
# Token resolution
# ======================================================================


def _resolve_token() -> tuple[str, str] | None:
    """Resolve a GitHub token from all available sources.

    Returns ``(token, source_label)`` or *None* if no token is found.
    The *source_label* is used for logging only.

    Resolution order matches the Copilot SDK's priority:
    1. ``COPILOT_GITHUB_TOKEN`` env var
    2. ``GH_TOKEN`` env var
    3. Copilot CLI keychain credential
    4. Legacy SDK config files (``hosts.json`` / ``apps.json``)
    5. ``gh auth token`` subprocess
    6. ``GITHUB_TOKEN`` env var
    """
    # 1. COPILOT_GITHUB_TOKEN (highest priority per SDK docs)
    copilot_env = os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()
    if copilot_env:
        return copilot_env, "env:COPILOT_GITHUB_TOKEN"

    # 2. GH_TOKEN (GitHub CLI compatible)
    gh_env = os.environ.get("GH_TOKEN", "").strip()
    if gh_env:
        return gh_env, "env:GH_TOKEN"

    # 3. Copilot CLI keychain (Windows Credential Manager / macOS Keychain)
    keychain_token = _read_keychain_token()
    if keychain_token:
        return keychain_token, "copilot-cli-keychain"

    # 4. Legacy SDK config files (hosts.json / apps.json)
    sdk_token = _read_oauth_token()
    if sdk_token:
        return sdk_token, "copilot-sdk-config"

    # 5. gh CLI subprocess (reads from credential manager)
    gh_token = _read_gh_token()
    if gh_token:
        return gh_token, "gh-cli"

    # 6. GITHUB_TOKEN (lowest priority)
    env_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_token:
        return env_token, "env:GITHUB_TOKEN"

    return None


# ======================================================================
# Public API
# ======================================================================


def get_copilot_token() -> str:
    """Get a raw GitHub OAuth/PAT token for Copilot.

    Tries all credential sources in priority order and returns
    whichever raw OAuth / PAT token is found.

    Returns:
        A raw GitHub token string (``gho_``, ``ghu_``, ``ghp_``, etc.).

    Raises:
        CLIError: If no credentials are found.
    """
    resolved = _resolve_token()
    if not resolved:
        raise CLIError(
            "No Copilot credentials found.\n\n"
            "To authenticate, do ONE of the following:\n"
            "  1. Run 'copilot login' (recommended)\n"
            "  2. Set COPILOT_GITHUB_TOKEN to a token with Copilot access\n"
            "  3. Use a different provider: --ai-provider github-models"
        )

    oauth_token, source = resolved
    logger.info("Resolved Copilot token from %s", source)
    return oauth_token


def is_copilot_authenticated() -> bool:
    """Check whether any GitHub credentials are available for Copilot."""
    return _resolve_token() is not None
