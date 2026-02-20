"""Check if token exchange JWT gives access to more models."""
import json, ctypes, ctypes.wintypes
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

token = get_keychain_token()
print(f"Keychain token prefix: {token[:12]}...")

# Try token exchange
print("\n── Token Exchange ──")
exchange_url = "https://api.github.com/copilot_internal/v2/token"
resp = requests.get(exchange_url, headers={
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Editor-Version": "vscode/1.100.0",
}, timeout=15)
print(f"  Status: {resp.status_code}")
print(f"  Body: {resp.text[:400]}")

if resp.status_code == 200:
    data = resp.json()
    jwt = data.get("token", "")
    endpoints = data.get("endpoints", {})
    print(f"\n  JWT prefix: {jwt[:30]}...")
    print(f"  Endpoints: {json.dumps(endpoints, indent=2)[:500] if endpoints else 'none'}")
    
    # Try the JWT with the API
    jwt_headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "GithubCopilot/1.300.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.37.5",
    }
    
    # Check /models with JWT
    print("\n── /models with JWT ──")
    mr = requests.get("https://api.githubcopilot.com/models", headers=jwt_headers, timeout=15)
    print(f"  Status: {mr.status_code}")
    if mr.status_code == 200:
        models = mr.json()
        if isinstance(models, dict) and "data" in models:
            for m in models["data"]:
                mid = m.get("id", m) if isinstance(m, dict) else m
                print(f"    - {mid}")
    
    # Test Claude with JWT
    print("\n── Claude test with JWT ──")
    for model in ["claude-sonnet-4.5", "claude-sonnet-4", "gpt-4o", "gpt-4.1"]:
        try:
            r = requests.post("https://api.githubcopilot.com/chat/completions",
                headers=jwt_headers,
                json={"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5},
                timeout=15)
            if r.status_code == 200:
                print(f"    {model:<30} OK")
            else:
                msg = ""
                try:
                    msg = json.loads(r.text).get("error", {}).get("message", "")[:60]
                except:
                    msg = r.text[:60]
                print(f"    {model:<30} {r.status_code} - {msg}")
        except requests.Timeout:
            print(f"    {model:<30} TIMEOUT")
        except Exception as e:
            print(f"    {model:<30} ERR - {e}")

# Also try alternative Copilot API endpoint patterns
print("\n── Alternative endpoints ──")
for ep in [
    "https://copilot-proxy.githubusercontent.com/v1/chat/completions",
    "https://api.individual.githubcopilot.com/chat/completions",
    "https://api.business.githubcopilot.com/chat/completions",
]:
    try:
        r = requests.post(ep, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "GithubCopilot/1.300.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
        }, json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}, timeout=10)
        print(f"  {ep}: {r.status_code}")
    except Exception as e:
        print(f"  {ep}: {type(e).__name__}")
