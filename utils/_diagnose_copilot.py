"""Detailed diagnostics: check each credential source and test against the API."""
import json, os, platform, subprocess, shutil, sys
from pathlib import Path

import requests

# ── Credential sources ────────────────────────────────────────────────

def get_copilot_cli_config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".copilot"

def get_copilot_config_dir():
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            return Path(local) / "github-copilot"
        return Path.home() / "AppData" / "Local" / "github-copilot"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "github-copilot"
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "github-copilot"
    return Path.home() / ".config" / "github-copilot"

def read_keychain_token():
    """Read from Windows Credential Manager."""
    if platform.system() != "Windows":
        return None, "not Windows"
    config_path = get_copilot_cli_config_dir() / "config.json"
    if not config_path.exists():
        return None, f"no config at {config_path}"
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"config read error: {e}"

    logins = []
    last_user = cfg.get("last_logged_in_user", {})
    if isinstance(last_user, dict) and last_user.get("login"):
        logins.append(last_user["login"])
    for user in cfg.get("logged_in_users", []):
        if isinstance(user, dict) and user.get("login"):
            login = user["login"]
            if login not in logins:
                logins.append(login)

    if not logins:
        return None, f"no logins in config (keys: {list(cfg.keys())})"

    try:
        import ctypes, ctypes.wintypes
        advapi32 = ctypes.windll.advapi32
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
            ok = advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
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
                    return token, f"keychain (login={login})"
            finally:
                advapi32.CredFree(cred_ptr)
        return None, f"no credential found for logins {logins}"
    except Exception as e:
        return None, f"ctypes error: {e}"

def read_sdk_config():
    """Read from hosts.json / apps.json."""
    config_dir = get_copilot_config_dir()
    for filename in ("hosts.json", "apps.json"):
        fpath = config_dir / filename
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        for _host, host_data in data.items():
            if isinstance(host_data, dict):
                token = host_data.get("oauth_token")
                if token:
                    return token, f"sdk-config ({filename})"
    return None, f"no tokens in {config_dir}"

def read_gh_cli():
    """Read from gh auth token."""
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), "gh-cli"
    except Exception as e:
        return None, f"gh error: {e}"
    return None, "gh returned empty"

# ── Test a token against the API ──────────────────────────────────────

def test_token(token, source, model="gpt-4o"):
    """Test a token against the Copilot completions API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "GithubCopilot/1.300.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.37.5",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hi"}],
        "max_tokens": 5,
    }
    try:
        resp = requests.post(
            "https://api.githubcopilot.com/chat/completions",
            headers=headers, json=payload, timeout=30,
        )
        return resp.status_code, resp.text[:300]
    except Exception as e:
        return -1, str(e)

# ── Main ──────────────────────────────────────────────────────────────

print("=" * 70)
print("COPILOT API DIAGNOSTIC")
print("=" * 70)

# Check env vars
print("\n── Environment Variables ──")
for var in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
    val = os.environ.get(var, "")
    if val:
        print(f"  {var}: set (prefix: {val[:12]}...)")
    else:
        print(f"  {var}: (not set)")

# Check config dirs
print("\n── Config Directories ──")
cli_dir = get_copilot_cli_config_dir()
sdk_dir = get_copilot_config_dir()
print(f"  Copilot CLI dir: {cli_dir} (exists: {cli_dir.exists()})")
print(f"  SDK config dir:  {sdk_dir} (exists: {sdk_dir.exists()})")
if cli_dir.exists():
    print(f"    Contents: {[f.name for f in cli_dir.iterdir()]}")
if sdk_dir.exists():
    print(f"    Contents: {[f.name for f in sdk_dir.iterdir()]}")

# Gather all tokens
print("\n── Token Resolution ──")
sources = []

env_copilot = os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()
if env_copilot:
    sources.append((env_copilot, "env:COPILOT_GITHUB_TOKEN"))

env_gh = os.environ.get("GH_TOKEN", "").strip()
if env_gh:
    sources.append((env_gh, "env:GH_TOKEN"))

kc_token, kc_info = read_keychain_token()
if kc_token:
    sources.append((kc_token, f"keychain: {kc_info}"))
else:
    print(f"  Keychain: {kc_info}")

sdk_token, sdk_info = read_sdk_config()
if sdk_token:
    sources.append((sdk_token, f"sdk: {sdk_info}"))
else:
    print(f"  SDK config: {sdk_info}")

gh_token, gh_info = read_gh_cli()
if gh_token:
    sources.append((gh_token, f"gh-cli: {gh_info}"))
else:
    print(f"  gh CLI: {gh_info}")

env_github = os.environ.get("GITHUB_TOKEN", "").strip()
if env_github:
    sources.append((env_github, "env:GITHUB_TOKEN"))

if not sources:
    print("\n  ERROR: No tokens found from any source!")
    sys.exit(1)

print(f"\n  Found {len(sources)} token source(s):")
for tok, src in sources:
    prefix = tok[:12] if len(tok) > 12 else tok[:4]
    print(f"    - {src}: {prefix}...")

# Test each token
print("\n── API Tests (using gpt-4o) ──")
for tok, src in sources:
    status, body = test_token(tok, src, "gpt-4o")
    prefix = tok[:12] if len(tok) > 12 else tok[:4]
    print(f"\n  Token from {src} ({prefix}...):")
    print(f"    Status: {status}")
    print(f"    Body:   {body[:200]}")

    if status == 200:
        # This one works! Test all models with it
        print("\n── Model availability (working token) ──")
        for m in ["claude-sonnet-4.5", "claude-sonnet-4", "gpt-4o", "gpt-4.1", "o3-mini", "gemini-2.5-pro"]:
            s, b = test_token(tok, src, m)
            try:
                err_msg = json.loads(b).get("error", {}).get("message", "")
            except:
                err_msg = b[:80]
            if s == 200:
                print(f"    {m:<30} OK")
            else:
                print(f"    {m:<30} {s} - {err_msg[:60]}")
        break
else:
    print("\n  No working token found. Trying token exchange approach...")
    
    # Try the copilot_internal token exchange as fallback
    for tok, src in sources:
        print(f"\n  Trying token exchange with {src}...")
        try:
            resp = requests.get(
                "https://api.github.com/copilot_internal/v2/token",
                headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
                timeout=10,
            )
            print(f"    Exchange status: {resp.status_code}")
            print(f"    Response: {resp.text[:200]}")
            if resp.status_code == 200:
                data = resp.json()
                jwt_token = data.get("token", "")
                if jwt_token:
                    print(f"    Got JWT token (prefix: {jwt_token[:20]}...)")
                    # Test with the JWT
                    s, b = test_token(jwt_token, "jwt-exchange", "gpt-4o")
                    print(f"    JWT test: status={s}, body={b[:150]}")
        except Exception as e:
            print(f"    Exchange error: {e}")

print("\n" + "=" * 70)
print("DONE")
