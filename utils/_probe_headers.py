"""Try different header combinations to see if newer versions unlock more models."""
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
URL = "https://api.githubcopilot.com/chat/completions"
MODELS_URL = "https://api.githubcopilot.com/models"
test_payload = {"model": "claude-sonnet-4.5", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}

HEADER_VARIANTS = [
    {
        "name": "VS Code 1.100 + copilot-chat 0.37",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GithubCopilot/1.300.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.37.5",
        },
    },
    {
        "name": "VS Code 1.97 + copilot 1.276",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GitHubCopilotChat/0.27.2",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.97.0",
            "Editor-Plugin-Version": "copilot/1.276.0",
            "Openai-Organization": "github-copilot",
            "Openai-Intent": "conversation-panel",
        },
    },
    {
        "name": "copilot-cli integration",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GithubCopilot/1.300.0",
            "Copilot-Integration-Id": "copilot-cli",
            "Editor-Version": "copilot-cli/0.3.0",
        },
    },
    {
        "name": "GitHub CLI Copilot extension",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GitHubCopilotCLI/0.3.0-beta",
            "Copilot-Integration-Id": "github.copilot-cli",
            "Editor-Version": "gh-copilot/0.3.0",
            "Editor-Plugin-Version": "gh-copilot/0.3.0",
        },
    },
    {
        "name": "Openai-Organization header",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GithubCopilot/1.300.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.37.5",
            "Openai-Organization": "github-copilot",
        },
    },
]

for variant in HEADER_VARIANTS:
    name = variant["name"]
    headers = variant["headers"]
    
    print(f"\n── {name} ──")
    
    # Check /models
    try:
        mr = requests.get(MODELS_URL, headers=headers, timeout=10)
        if mr.status_code == 200:
            data = mr.json()
            model_ids = []
            if isinstance(data, dict) and "data" in data:
                model_ids = [m.get("id", str(m)) for m in data["data"] if isinstance(m, dict)]
            elif isinstance(data, list):
                model_ids = [m.get("id", str(m)) for m in data if isinstance(m, dict)]
            print(f"  /models: {mr.status_code} - {len(model_ids)} models: {model_ids}")
        else:
            print(f"  /models: {mr.status_code}")
    except Exception as e:
        print(f"  /models: {type(e).__name__}")
    
    # Test claude
    try:
        r = requests.post(URL, headers=headers, json=test_payload, timeout=15)
        if r.status_code == 200:
            print(f"  claude-sonnet-4.5: OK!")
        else:
            msg = ""
            try:
                msg = json.loads(r.text).get("error", {}).get("message", "")[:80]
            except:
                pass
            print(f"  claude-sonnet-4.5: {r.status_code} - {msg}")
    except requests.Timeout:
        print(f"  claude-sonnet-4.5: TIMEOUT")
    except Exception as e:
        print(f"  claude-sonnet-4.5: {type(e).__name__}")
