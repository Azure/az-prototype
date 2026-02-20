"""Part 2: remaining checks that didn't complete in the first run."""
import json, ctypes, ctypes.wintypes, subprocess
from pathlib import Path
import requests
from requests.exceptions import ConnectionError as ConnErr, Timeout

def get_keychain_token():
    config_path = Path.home() / ".copilot" / "config.json"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    last_user = cfg.get("last_logged_in_user", {})
    login = last_user.get("login", "")
    advapi32 = ctypes.windll.advapi32
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.wintypes.DWORD), ("Type", ctypes.wintypes.DWORD),
            ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
            ("LastWritten", ctypes.wintypes.FILETIME),
            ("CredentialBlobSize", ctypes.wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", ctypes.wintypes.DWORD),
            ("AttributeCount", ctypes.wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p),
        ]
    cred_ptr = ctypes.POINTER(CREDENTIAL)()
    host = last_user.get("host", "https://github.com").rstrip("/")
    target = f"copilot-cli/{host}:{login}"
    ok = advapi32.CredReadW(target, 1, 0, ctypes.byref(cred_ptr))
    if ok:
        cred = cred_ptr.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        advapi32.CredFree(cred_ptr)
        return blob.decode("utf-8", errors="replace").strip()
    raise RuntimeError("No keychain token")

copilot_token = get_keychain_token()
gh_token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()

COPILOT_HEADERS = {
    "Authorization": f"Bearer {copilot_token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.100.0",
    "Editor-Plugin-Version": "copilot-chat/0.37.5",
}

GH_HEADERS = {
    "Authorization": f"Bearer {gh_token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Session with aggressive timeouts
session = requests.Session()
session.verify = True

def safe_post(url, headers, payload, timeout=8):
    try:
        r = session.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            return f"OK - model={r.json().get('model', '?')}"
        try:
            msg = r.json().get("error", {}).get("message", r.text[:80])[:80]
        except: msg = r.text[:80]
        return f"{r.status_code} - {msg}"
    except ConnErr: return "CONN_ERR"
    except Timeout: return "TIMEOUT"
    except Exception as e: return f"ERR - {type(e).__name__}"

def safe_get(url, headers, timeout=8):
    try:
        r = session.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            entries = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(entries, list):
                ids = [e.get("id", "?") for e in entries if isinstance(e, dict)]
                return f"OK ({len(ids)}): {ids}"
            return f"OK: {str(data)[:150]}"
        return f"{r.status_code} - {r.text[:80]}"
    except ConnErr: return "CONN_ERR"
    except Timeout: return "TIMEOUT"
    except Exception as e: return f"ERR - {type(e).__name__}"

claude_payload = {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}

# ── Remaining completions endpoints from first run ──
print("── REMAINING COMPLETIONS ENDPOINTS ──\n")
remaining_completions = [
    ("https://models.inference.ai.azure.com/chat/completions", GH_HEADERS),
    ("https://api.githubcopilot.com/agents/chat/completions", COPILOT_HEADERS),
    ("https://api.githubcopilot.com/workspace/chat/completions", COPILOT_HEADERS),
    ("https://api.githubcopilot.com/preview/chat/completions", COPILOT_HEADERS),
    ("https://api.githubcopilot.com/beta/chat/completions", COPILOT_HEADERS),
    ("https://api.githubcopilot.com/anthropic/chat/completions", COPILOT_HEADERS),
]
for url, hdrs in remaining_completions:
    r = safe_post(url, hdrs, claude_payload)
    print(f"  {url:<65} {r}")

# ── Enterprise endpoint was interesting (got 400 not 421) - test with gpt-4o ──
print("\n── ENTERPRISE ENDPOINT (gpt-4o test) ──\n")
gpt_payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
r = safe_post("https://api.enterprise.githubcopilot.com/chat/completions", COPILOT_HEADERS, gpt_payload)
print(f"  gpt-4o on enterprise endpoint: {r}")

# Also check its /models
r = safe_get("https://api.enterprise.githubcopilot.com/models", COPILOT_HEADERS)
print(f"  enterprise /models: {r}")

# ── /models catalogue on endpoints we know respond ──
print("\n── MODEL CATALOGUES ──\n")
model_urls = [
    ("https://api.githubcopilot.com/models", COPILOT_HEADERS),
    ("https://api.enterprise.githubcopilot.com/models", COPILOT_HEADERS),
    ("https://api.github.com/copilot/models", COPILOT_HEADERS),
    ("https://api.github.com/copilot_internal/models", COPILOT_HEADERS),
    ("https://models.inference.ai.azure.com/models", GH_HEADERS),
]
for url, hdrs in model_urls:
    r = safe_get(url, hdrs)
    print(f"  {url:<60} {r[:130]}")

# ── Token exchange ──
print("\n── TOKEN EXCHANGE ──\n")
exchange_urls = [
    "https://api.github.com/copilot_internal/v2/token",
    "https://api.github.com/copilot_internal/token",
    "https://api.github.com/copilot/token",
    "https://api.githubcopilot.com/token",
]
for url in exchange_urls:
    for label, tok in [("keychain", copilot_token), ("gh-cli", gh_token)]:
        try:
            r = session.get(url, headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/json",
                "User-Agent": "GithubCopilot/1.300.0",
                "Editor-Version": "vscode/1.100.0",
            }, timeout=8)
            if r.status_code == 200:
                data = r.json()
                jwt = data.get("token", "")
                print(f"  ✓ {url} ({label}): 200")
                print(f"    JWT prefix: {jwt[:40]}...")
                # Test Claude with JWT
                jwt_hdrs = {**COPILOT_HEADERS, "Authorization": f"Bearer {jwt}"}
                cr = safe_post("https://api.githubcopilot.com/chat/completions", jwt_hdrs, claude_payload)
                print(f"    Claude via JWT: {cr}")
                mr = safe_get("https://api.githubcopilot.com/models", jwt_hdrs)
                print(f"    /models via JWT: {mr[:120]}")
            else:
                print(f"  ✗ {url} ({label}): {r.status_code}")
        except Exception as e:
            print(f"  ✗ {url} ({label}): {type(e).__name__}")

# ── Claude model ID variants ──
print("\n── CLAUDE MODEL ID VARIANTS ──\n")
claude_ids = [
    "claude-sonnet-4", "claude-sonnet-4.5", "claude-opus-4", "claude-opus-4.5",
    "claude-haiku-4.5",
    "anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4.5",
    "anthropic.claude-sonnet-4",
    "claude-3.5-sonnet", "claude-3.5-sonnet-20241022",
    "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-latest",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-sonnet-4-20250514",
    "copilot-claude-sonnet-4", "github-claude-sonnet-4",
]
for mid in claude_ids:
    p = {"model": mid, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
    r = safe_post("https://api.githubcopilot.com/chat/completions", COPILOT_HEADERS, p)
    icon = "✓" if r.startswith("OK") else "✗"
    print(f"  {icon} {mid:<45} {r}")

# ── gh copilot extensions ──
print("\n── gh extensions ──")
try:
    r = subprocess.run(["gh", "extension", "list"], capture_output=True, text=True, timeout=10)
    print(f"  {r.stdout.strip() or '(none)'}")
except: print("  (failed)")

print("\n" + "=" * 80)
print("DONE")
