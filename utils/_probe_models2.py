"""Fast model probe - remaining models + /models endpoint."""
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
HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.100.0",
    "Editor-Plugin-Version": "copilot-chat/0.37.5",
}
URL = "https://api.githubcopilot.com/chat/completions"

# Check /models endpoint first
print("── /models endpoint ──")
for ep in ["https://api.githubcopilot.com/models", "https://api.githubcopilot.com/v1/models"]:
    try:
        resp = requests.get(ep, headers=HEADERS, timeout=15)
        print(f"  {ep}: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                for m in data["data"]:
                    mid = m.get("id", m) if isinstance(m, dict) else m
                    print(f"    - {mid}")
            elif isinstance(data, list):
                for m in data:
                    mid = m.get("id", m) if isinstance(m, dict) else m
                    print(f"    - {mid}")
            else:
                print(f"    {json.dumps(data, indent=2)[:500]}")
        else:
            print(f"    {resp.text[:200]}")
    except Exception as e:
        print(f"    Error: {e}")

# Models not yet tested
REMAINING = [
    "gpt-4.5-preview",
    "o1", "o1-mini", "o1-preview", "o3", "o3-mini", "o4-mini",
    "gpt-3.5-turbo",
    "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-sonnet-4", "claude-opus-4", "claude-opus-4.5", "claude-haiku-4.5",
    "gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-pro",
    "copilot-chat",
]

print("\n── Model tests ──")
payload_template = {"messages": [{"role": "user", "content": "Say hi"}], "max_tokens": 5, "temperature": 0.0}

for model in REMAINING:
    payload = {**payload_template, "model": model}
    try:
        resp = requests.post(URL, headers=HEADERS, json=payload, timeout=15)
        if resp.status_code == 200:
            actual = resp.json().get("model", "?")
            print(f"  {model:<35} OK (actual: {actual})")
        else:
            try:
                msg = json.loads(resp.text).get("error", {}).get("message", "")[:60]
            except:
                msg = resp.text[:60]
            print(f"  {model:<35} {resp.status_code} - {msg}")
    except requests.Timeout:
        print(f"  {model:<35} TIMEOUT")
    except Exception as e:
        print(f"  {model:<35} ERR - {e}")

# Summary from all tests
print("\n── CONFIRMED WORKING ──")
print("  gpt-4o                 (actual: gpt-4o-2024-11-20)")
print("  gpt-4o-mini            (actual: gpt-4o-mini-2024-07-18)")
print("  gpt-4o-2024-08-06")
print("  gpt-4o-2024-11-20")
