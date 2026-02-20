"""Test all Claude model ID variants against the main Copilot API."""
import json, ctypes, ctypes.wintypes, requests
from pathlib import Path

def get_kc():
    cfg = json.loads((Path.home() / ".copilot" / "config.json").read_text())
    lu = cfg.get("last_logged_in_user", {})
    a = ctypes.windll.advapi32
    class C(ctypes.Structure):
        _fields_ = [
            ("F", ctypes.wintypes.DWORD), ("T", ctypes.wintypes.DWORD),
            ("TN", ctypes.c_wchar_p), ("Co", ctypes.c_wchar_p),
            ("LW", ctypes.wintypes.FILETIME),
            ("CBS", ctypes.wintypes.DWORD),
            ("CB", ctypes.POINTER(ctypes.c_ubyte)),
            ("P", ctypes.wintypes.DWORD), ("AC", ctypes.wintypes.DWORD),
            ("At", ctypes.c_void_p),
            ("TA", ctypes.c_wchar_p), ("UN", ctypes.c_wchar_p),
        ]
    p = ctypes.POINTER(C)()
    h = lu.get("host", "https://github.com").rstrip("/")
    login = lu.get("login", "")
    t = f"copilot-cli/{h}:{login}"
    if a.CredReadW(t, 1, 0, ctypes.byref(p)):
        c = p.contents
        b = ctypes.string_at(c.CB, c.CBS)
        a.CredFree(p)
        return b.decode("utf-8", errors="replace").strip()
    raise RuntimeError("no token")

token = get_kc()
H = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.100.0",
    "Editor-Plugin-Version": "copilot-chat/0.37.5",
}
U = "https://api.githubcopilot.com/chat/completions"

claude_ids = [
    "claude-sonnet-4", "claude-sonnet-4.5", "claude-opus-4", "claude-opus-4.5",
    "claude-haiku-4.5",
    "anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4.5",
    "claude-3.5-sonnet", "claude-3.5-sonnet-20241022",
    "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-latest",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-sonnet-4-20250514", "copilot-claude-sonnet-4",
    "github-claude-sonnet-4",
]

print("Model ID Variants on api.githubcopilot.com/chat/completions:")
print("-" * 70)
for mid in claude_ids:
    try:
        r = requests.post(
            U, headers=H,
            json={"model": mid, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5},
            timeout=8,
        )
        if r.status_code == 200:
            print(f"  OK  {mid:<45} model={r.json().get('model', '?')}")
        else:
            try:
                msg = r.json().get("error", {}).get("message", "")[:60]
            except Exception:
                msg = r.text[:60]
            print(f"  {r.status_code:3d} {mid:<45} {msg}")
    except requests.ConnectionError:
        print(f"  ERR {mid:<45} connection error")
    except requests.Timeout:
        print(f"  ERR {mid:<45} timeout")
    except Exception as e:
        print(f"  ERR {mid:<45} {e}")

# Also try the copilot-proxy endpoint with proper token format
print("\n\ncopilot-proxy.githubusercontent.com (with different auth):")
print("-" * 70)
for mid in ["claude-sonnet-4", "gpt-4o"]:
    for auth_fmt in [f"Bearer {token}", f"token {token}"]:
        hdrs = {**H, "Authorization": auth_fmt}
        try:
            r = requests.post(
                "https://copilot-proxy.githubusercontent.com/v1/chat/completions",
                headers=hdrs,
                json={"model": mid, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5},
                timeout=8,
            )
            fmt_label = auth_fmt.split()[0]
            if r.status_code == 200:
                print(f"  OK  {mid} ({fmt_label})")
            else:
                print(f"  {r.status_code:3d} {mid} ({fmt_label}): {r.text[:80]}")
        except Exception as e:
            print(f"  ERR {mid}: {type(e).__name__}")
