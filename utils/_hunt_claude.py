"""Exhaustive search for Claude on any GitHub/Copilot API endpoint."""
import json, ctypes, ctypes.wintypes, subprocess
from pathlib import Path
import requests

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

claude_payload = {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
gpt_payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}

def try_endpoint(url, headers, payload, label):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=12)
        status = r.status_code
        body = r.text[:150]
        try:
            msg = json.loads(r.text).get("error", {}).get("message", body)[:100]
        except:
            msg = body
        if status == 200:
            return f"OK - {r.json().get('model', '?')}"
        return f"{status} - {msg}"
    except requests.ConnectionError:
        return "CONN_ERR (host not found)"
    except requests.Timeout:
        return "TIMEOUT"
    except Exception as e:
        return f"ERR - {type(e).__name__}: {e}"

def try_models_endpoint(url, headers, label):
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            entries = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(entries, list):
                ids = [e.get("id", str(e))[:50] for e in entries if isinstance(e, dict)]
                return f"OK - {len(ids)} models: {ids}"
            return f"OK - {str(data)[:200]}"
        return f"{r.status_code} - {r.text[:100]}"
    except requests.ConnectionError:
        return "CONN_ERR"
    except requests.Timeout:
        return "TIMEOUT"
    except Exception as e:
        return f"ERR - {e}"

print("=" * 80)
print("EXHAUSTIVE CLAUDE ENDPOINT SEARCH")
print("=" * 80)

# ── 1. Completions endpoints ──
print("\n── COMPLETIONS ENDPOINTS (Claude payload) ──\n")

completions_endpoints = [
    # Main Copilot API
    ("api.githubcopilot.com/chat/completions", COPILOT_HEADERS),
    # Versioned paths
    ("api.githubcopilot.com/v1/chat/completions", COPILOT_HEADERS),
    ("api.githubcopilot.com/v2/chat/completions", COPILOT_HEADERS),
    # Sub-domain variants
    ("api.individual.githubcopilot.com/chat/completions", COPILOT_HEADERS),
    ("api.business.githubcopilot.com/chat/completions", COPILOT_HEADERS),
    ("api.enterprise.githubcopilot.com/chat/completions", COPILOT_HEADERS),
    # Proxy endpoints (used by some Copilot extensions)
    ("copilot-proxy.githubusercontent.com/v1/chat/completions", COPILOT_HEADERS),
    ("copilot-proxy.githubusercontent.com/chat/completions", COPILOT_HEADERS),
    # GitHub API sub-paths
    ("api.github.com/copilot/chat/completions", COPILOT_HEADERS),
    ("api.github.com/copilot_internal/chat/completions", COPILOT_HEADERS),
    # GitHub Models
    ("models.inference.ai.azure.com/chat/completions", GH_HEADERS),
    # Azure AI inference (used by GitHub Models v2)
    ("inference.ai.azure.com/chat/completions", GH_HEADERS),
    # GitHub Copilot Workspace / Agent
    ("api.githubcopilot.com/agents/chat/completions", COPILOT_HEADERS),
    ("api.githubcopilot.com/workspace/chat/completions", COPILOT_HEADERS),
    # Anthropic-specific proxy (speculative)
    ("anthropic-proxy.githubcopilot.com/chat/completions", COPILOT_HEADERS),
    ("claude-proxy.githubcopilot.com/chat/completions", COPILOT_HEADERS),
    ("api.githubcopilot.com/anthropic/chat/completions", COPILOT_HEADERS),
    # Preview / beta
    ("api.githubcopilot.com/preview/chat/completions", COPILOT_HEADERS),
    ("api.githubcopilot.com/beta/chat/completions", COPILOT_HEADERS),
]

for url, headers in completions_endpoints:
    full_url = f"https://{url}"
    result = try_endpoint(full_url, headers, claude_payload, "claude")
    status_icon = "✓" if result.startswith("OK") else "✗"
    print(f"  {status_icon} {url:<62} {result}")

# ── 2. Model catalogue endpoints ──
print("\n── MODEL CATALOGUE ENDPOINTS ──\n")

model_endpoints = [
    ("api.githubcopilot.com/models", COPILOT_HEADERS),
    ("api.githubcopilot.com/v1/models", COPILOT_HEADERS),
    ("api.githubcopilot.com/v2/models", COPILOT_HEADERS),
    ("api.github.com/copilot/models", COPILOT_HEADERS),
    ("api.github.com/copilot_internal/models", COPILOT_HEADERS),
    ("copilot-proxy.githubusercontent.com/v1/models", COPILOT_HEADERS),
    ("models.inference.ai.azure.com/models", GH_HEADERS),
]

for url, headers in model_endpoints:
    full_url = f"https://{url}"
    result = try_models_endpoint(full_url, headers, url)
    status_icon = "✓" if result.startswith("OK") else "✗"
    print(f"  {status_icon} {url:<62} {result[:120]}")

# ── 3. Token exchange endpoints (might reveal different model sets) ──
print("\n── TOKEN EXCHANGE ENDPOINTS ──\n")

exchange_endpoints = [
    "https://api.github.com/copilot_internal/v2/token",
    "https://api.github.com/copilot_internal/token",
    "https://api.github.com/copilot/token",
    "https://api.githubcopilot.com/token",
    "https://api.githubcopilot.com/v1/token",
    "https://copilot-proxy.githubusercontent.com/v1/token",
]

for url in exchange_endpoints:
    for tok_label, tok in [("copilot-keychain", copilot_token), ("gh-cli", gh_token)]:
        try:
            r = requests.get(url, headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/json",
                "User-Agent": "GithubCopilot/1.300.0",
                "Editor-Version": "vscode/1.100.0",
            }, timeout=8)
            if r.status_code == 200:
                data = r.json()
                jwt = data.get("token", "")[:30]
                endpoints_data = data.get("endpoints", "")
                print(f"  ✓ {url} ({tok_label}): 200 - jwt={jwt}... endpoints={str(endpoints_data)[:80]}")
                
                # If we got a JWT, test it against Claude
                jwt_full = data.get("token", "")
                if jwt_full:
                    jwt_headers = {**COPILOT_HEADERS, "Authorization": f"Bearer {jwt_full}"}
                    cr = try_endpoint("https://api.githubcopilot.com/chat/completions", jwt_headers, claude_payload, "claude-jwt")
                    print(f"       → Claude via JWT: {cr}")
                    # Also check /models with JWT
                    mr = try_models_endpoint("https://api.githubcopilot.com/models", jwt_headers, "models-jwt")
                    print(f"       → /models via JWT: {mr[:120]}")
            else:
                print(f"  ✗ {url} ({tok_label}): {r.status_code} - {r.text[:80]}")
        except requests.ConnectionError:
            print(f"  ✗ {url} ({tok_label}): CONN_ERR")
        except requests.Timeout:
            print(f"  ✗ {url} ({tok_label}): TIMEOUT")
        except Exception as e:
            print(f"  ✗ {url} ({tok_label}): {e}")

# ── 4. Try Claude with different model ID formats on the main endpoint ──
print("\n── CLAUDE MODEL ID VARIANTS (main endpoint) ──\n")

claude_variants = [
    "claude-sonnet-4", "claude-sonnet-4.5", "claude-opus-4", "claude-opus-4.5",
    "claude-haiku-4.5",
    "anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4.5",
    "anthropic.claude-sonnet-4", "anthropic:claude-sonnet-4",
    "claude-3.5-sonnet", "claude-3.5-sonnet-20241022",
    "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-latest",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-sonnet-4-20250514",
    # VS Code Copilot chat might use these IDs
    "copilot-claude-sonnet-4", "github-claude-sonnet-4",
]

for model_id in claude_variants:
    payload = {"model": model_id, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
    result = try_endpoint("https://api.githubcopilot.com/chat/completions", COPILOT_HEADERS, payload, model_id)
    status_icon = "✓" if result.startswith("OK") else "✗"
    print(f"  {status_icon} {model_id:<45} {result}")

# ── 5. Check if the gh CLI copilot extension uses a different path ──
print("\n── gh copilot suggest endpoint (via gh CLI) ──\n")
try:
    r = subprocess.run(["gh", "extension", "list"], capture_output=True, text=True, timeout=10)
    print(f"  Installed extensions: {r.stdout.strip()}")
except Exception as e:
    print(f"  gh extension list: {e}")

print("\n" + "=" * 80)
print("DONE")
