"""Test the Model Lab endpoint discovered in VS Code Copilot Chat extension."""
import json, ctypes, ctypes.wintypes, requests
from pathlib import Path

def get_kc():
    cfg = json.loads((Path.home() / ".copilot" / "config.json").read_text())
    lu = cfg.get("last_logged_in_user", {})
    login = lu.get("login", "")
    a = ctypes.windll.advapi32
    class C(ctypes.Structure):
        _fields_ = [
            ("F", ctypes.wintypes.DWORD), ("T", ctypes.wintypes.DWORD),
            ("TN", ctypes.c_wchar_p), ("Co", ctypes.c_wchar_p),
            ("LW", ctypes.wintypes.FILETIME),
            ("CBS", ctypes.wintypes.DWORD), ("CB", ctypes.POINTER(ctypes.c_ubyte)),
            ("P", ctypes.wintypes.DWORD), ("AC", ctypes.wintypes.DWORD),
            ("At", ctypes.c_void_p),
            ("TA", ctypes.c_wchar_p), ("UN", ctypes.c_wchar_p),
        ]
    p = ctypes.POINTER(C)()
    h = lu.get("host", "https://github.com").rstrip("/")
    t = f"copilot-cli/{h}:{login}"
    if a.CredReadW(t, 1, 0, ctypes.byref(p)):
        c = p.contents; b = ctypes.string_at(c.CB, c.CBS); a.CredFree(p)
        return b.decode("utf-8", errors="replace").strip()
    raise RuntimeError("no token")

token = get_kc()

HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.100.0",
    "Editor-Plugin-Version": "copilot-chat/0.37.5",
}

MODEL_LAB_URL = "https://api-model-lab.githubcopilot.com"
MAIN_URL = "https://api.githubcopilot.com"

# 1. Check /models on Model Lab
print("=" * 70)
print("MODEL LAB ENDPOINT: " + MODEL_LAB_URL)
print("=" * 70)

print("\n── /models ──")
try:
    r = requests.get(f"{MODEL_LAB_URL}/models", headers=HEADERS, timeout=15)
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        entries = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(entries, list):
            print(f"  Count: {len(entries)} models")
            for e in entries:
                if isinstance(e, dict):
                    mid = e.get("id", "?")
                    name = e.get("name", "")
                    vendor = e.get("vendor", e.get("provider", ""))
                    version = e.get("version", "")
                    caps = e.get("capabilities", {})
                    print(f"    {mid:<40} vendor={vendor} name={name}")
                else:
                    print(f"    {e}")
        else:
            print(f"  Response: {json.dumps(data, indent=2)[:1000]}")
    else:
        print(f"  Response: {r.text[:500]}")
except Exception as e:
    print(f"  Error: {e}")

# 2. Test Claude on Model Lab
print("\n── Claude on Model Lab ──")
for model_id in ["claude-sonnet-4", "claude-sonnet-4.5", "claude-opus-4.5", "gpt-4o", "gpt-4.1", "o3-mini", "gemini-2.5-pro"]:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Say hi in one word."}],
        "max_tokens": 10,
    }
    try:
        r = requests.post(f"{MODEL_LAB_URL}/chat/completions", headers=HEADERS, json=payload, timeout=15)
        if r.status_code == 200:
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:30]
            actual = data.get("model", "?")
            print(f"  OK  {model_id:<30} actual={actual}, content={content}")
        else:
            try:
                msg = r.json().get("error", {}).get("message", "")[:80]
            except:
                msg = r.text[:80]
            print(f"  {r.status_code:3d} {model_id:<30} {msg}")
    except requests.Timeout:
        print(f"  TMO {model_id:<30} timeout")
    except Exception as e:
        print(f"  ERR {model_id:<30} {e}")

# 3. Also check /models/session (auto-model endpoint)
print("\n── /models/session (auto-model) ──")
try:
    r = requests.get(f"{MODEL_LAB_URL}/models/session", headers=HEADERS, timeout=10)
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        print(f"  Response: {r.text[:500]}")
    else:
        print(f"  {r.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# 4. Compare with main API
print(f"\n── Comparison: {MAIN_URL}/models ──")
try:
    r = requests.get(f"{MAIN_URL}/models", headers=HEADERS, timeout=10)
    if r.status_code == 200:
        data = r.json()
        entries = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(entries, list):
            ids = [e.get("id") for e in entries if isinstance(e, dict)]
            print(f"  Main API: {len(ids)} models: {ids}")
except Exception as e:
    print(f"  Error: {e}")

print("\nDONE")
