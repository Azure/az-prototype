"""Probe Copilot API for all potentially available models using the keychain token."""
import json, os, platform, subprocess, ctypes, ctypes.wintypes
from pathlib import Path
import requests

# ── Get keychain token ──────────────────────────────────────────────
def get_keychain_token():
    config_path = Path.home() / ".copilot" / "config.json"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    logins = []
    last_user = cfg.get("last_logged_in_user", {})
    if isinstance(last_user, dict) and last_user.get("login"):
        logins.append(last_user["login"])
    
    advapi32 = ctypes.windll.advapi32
    CRED_TYPE_GENERIC = 1
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
    for login in logins:
        host = last_user.get("host", "https://github.com").rstrip("/")
        target = f"copilot-cli/{host}:{login}"
        ok = advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
        if ok:
            cred = cred_ptr.contents
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            advapi32.CredFree(cred_ptr)
            return blob.decode("utf-8", errors="replace").strip()
    raise RuntimeError("No keychain token found")

token = get_keychain_token()
print(f"Token prefix: {token[:12]}...\n")

URL = "https://api.githubcopilot.com/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.100.0",
    "Editor-Plugin-Version": "copilot-chat/0.37.5",
}

# Comprehensive list of models to probe
MODELS = [
    # OpenAI GPT-4 family
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
    "gpt-4o-2024-08-06", "gpt-4o-2024-11-20",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-4.5-preview",
    # OpenAI o-series
    "o1", "o1-mini", "o1-preview",
    "o3", "o3-mini",
    "o4-mini",
    # OpenAI GPT-3.5
    "gpt-3.5-turbo",
    # Claude (various naming formats)
    "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-sonnet-4", "claude-sonnet-4.5", "claude-opus-4", "claude-opus-4.5",
    "claude-haiku-4.5",
    "anthropic/claude-3.5-sonnet", "anthropic/claude-sonnet-4",
    # Gemini
    "gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-pro",
    "gemini-2.0-flash-001",
    "google/gemini-2.5-pro", "google/gemini-2.0-flash",
    # Copilot-specific
    "copilot-chat", "copilot",
]

payload_template = {
    "messages": [{"role": "user", "content": "Say hi"}],
    "max_tokens": 5,
    "temperature": 0.0,
}

working = []
print(f"{'Model':<40} {'Status':<8} {'Detail'}")
print("-" * 90)

for model in MODELS:
    payload = {**payload_template, "model": model}
    try:
        resp = requests.post(URL, headers=HEADERS, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:30]
            actual_model = data.get("model", "?")
            print(f"{model:<40} {'OK':<8} model={actual_model}, resp={content}")
            working.append((model, actual_model))
        else:
            try:
                err = json.loads(resp.text).get("error", {}).get("message", "")[:60]
            except:
                err = resp.text[:60]
            print(f"{model:<40} {resp.status_code:<8} {err}")
    except Exception as e:
        print(f"{model:<40} {'ERR':<8} {e}")

print("\n" + "=" * 70)
print(f"WORKING MODELS ({len(working)}):")
for req_model, actual_model in working:
    print(f"  {req_model:<40} -> {actual_model}")

# Also try the models endpoint
print("\n── Checking /models endpoint ──")
for endpoint in [
    "https://api.githubcopilot.com/models",
    "https://api.githubcopilot.com/v1/models",
]:
    try:
        resp = requests.get(endpoint, headers=HEADERS, timeout=15)
        print(f"  {endpoint}: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                for m in data["data"]:
                    print(f"    - {m.get('id', m)}")
            else:
                print(f"    {json.dumps(data, indent=2)[:500]}")
        else:
            print(f"    {resp.text[:200]}")
    except Exception as e:
        print(f"    Error: {e}")
