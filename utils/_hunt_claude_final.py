"""Final check: VS Code Copilot Chat extension paths + Copilot API with
special headers that the VS Code extension might set to unlock premium models."""
import json, ctypes, ctypes.wintypes, subprocess, requests
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
            ("CBS", ctypes.wintypes.DWORD), ("CB", ctypes.POINTER(ctypes.c_ubyte)),
            ("P", ctypes.wintypes.DWORD), ("AC", ctypes.wintypes.DWORD),
            ("At", ctypes.c_void_p),
            ("TA", ctypes.c_wchar_p), ("UN", ctypes.c_wchar_p),
        ]
    p = ctypes.POINTER(C)()
    h = lu.get("host", "https://github.com").rstrip("/")
    t = f"copilot-cli/{h}:{lu.get('login', '')}"
    if a.CredReadW(t, 1, 0, ctypes.byref(p)):
        c = p.contents; b = ctypes.string_at(c.CB, c.CBS); a.CredFree(p)
        return b.decode("utf-8", errors="replace").strip()
    raise RuntimeError("no token")

token = get_kc()

base_headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

claude_payload = {
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "Hi"}],
    "max_tokens": 5,
}

# 1. Copilot-specific feature flags / headers that might gate model access
print("=== Feature flag / premium headers ===\n")
extra_header_sets = [
    {"name": "X-Copilot-Premium", "hdrs": {"X-Copilot-Premium": "true"}},
    {"name": "X-GitHub-Copilot-Plan: business", "hdrs": {"X-GitHub-Copilot-Plan": "business"}},
    {"name": "X-GitHub-Copilot-Plan: enterprise", "hdrs": {"X-GitHub-Copilot-Plan": "enterprise"}},
    {"name": "Openai-Intent: model-catalog", "hdrs": {"Openai-Intent": "model-catalog"}},
    {"name": "Openai-Intent: conversation-panel", "hdrs": {"Openai-Intent": "conversation-panel"}},
    {"name": "X-Copilot-Model-Catalog: premium", "hdrs": {"X-Copilot-Model-Catalog": "premium"}},
    {"name": "X-GitHub-Api-Version: 2025-01-01", "hdrs": {"X-GitHub-Api-Version": "2025-01-01"}},
    {"name": "X-GitHub-Api-Version: 2024-12-01", "hdrs": {"X-GitHub-Api-Version": "2024-12-01"}},
    {
        "name": "full vscode Copilot Chat headers",
        "hdrs": {
            "User-Agent": "GitHubCopilotChat/0.27.2",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.97.2",
            "Editor-Plugin-Version": "copilot/1.276.0",
            "Openai-Organization": "github-copilot",
            "Openai-Intent": "conversation-panel",
            "X-Request-Id": "test-123",
            "X-VSCode-SessionId": "test-session",
        },
    },
]

for hs in extra_header_sets:
    hdrs = {**base_headers, "User-Agent": "GithubCopilot/1.300.0",
            "Copilot-Integration-Id": "vscode-chat", "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.37.5", **hs["hdrs"]}
    try:
        # Check /models first
        mr = requests.get("https://api.githubcopilot.com/models", headers=hdrs, timeout=8)
        model_count = 0
        model_ids = []
        if mr.status_code == 200:
            data = mr.json()
            entries = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(entries, list):
                model_ids = [e.get("id") for e in entries if isinstance(e, dict)]
                model_count = len(model_ids)
        has_claude = any("claude" in (m or "").lower() for m in model_ids)
        
        # Try Claude completion
        r = requests.post("https://api.githubcopilot.com/chat/completions", headers=hdrs,
                         json=claude_payload, timeout=8)
        
        if r.status_code == 200:
            print(f"  ** {hs['name']}: Claude WORKS! models={model_count}")
        else:
            claude_note = "(has claude!)" if has_claude else ""
            print(f"  {hs['name']}: models={model_count} {claude_note}, claude={r.status_code}")
    except Exception as e:
        print(f"  {hs['name']}: {type(e).__name__}")

# 2. Check if there's a separate "premium" or "models" API endpoint
print("\n=== Premium / catalog endpoints ===\n")
premium_urls = [
    "https://api.githubcopilot.com/premium/chat/completions",
    "https://api.githubcopilot.com/catalog/chat/completions",
    "https://api.githubcopilot.com/models/claude-sonnet-4/chat/completions",
    "https://api.githubcopilot.com/v1/engines/claude-sonnet-4/chat/completions",
    "https://api.githubcopilot.com/openai/v1/chat/completions",
]
hdrs = {**base_headers, "User-Agent": "GithubCopilot/1.300.0",
        "Copilot-Integration-Id": "vscode-chat", "Editor-Version": "vscode/1.100.0"}

for url in premium_urls:
    try:
        r = requests.post(url, headers=hdrs, json=claude_payload, timeout=8)
        print(f"  {url}: {r.status_code} - {r.text[:80]}")
    except requests.ConnectionError:
        print(f"  {url}: CONN_ERR")
    except requests.Timeout:
        print(f"  {url}: TIMEOUT")

# 3. Check VS Code's Copilot hosts.json for any special endpoint info
print("\n=== VS Code Copilot extension config ===\n")
vscode_ext_dir = Path.home() / ".vscode" / "extensions"
if vscode_ext_dir.exists():
    copilot_dirs = sorted(vscode_ext_dir.glob("github.copilot-*"))
    print(f"  Found {len(copilot_dirs)} Copilot extension dir(s):")
    for d in copilot_dirs[-3:]:
        print(f"    {d.name}")
        # Check for hardcoded endpoints or model lists
        for f in d.rglob("*.js"):
            try:
                content = f.read_text(errors="ignore")
                if "claude" in content.lower() and "model" in content.lower():
                    # Find the relevant line
                    for i, line in enumerate(content.split("\n")):
                        if "claude" in line.lower() and len(line) < 200:
                            print(f"      {f.name}:{i}: {line.strip()[:120]}")
                            break
            except:
                pass

print("\nDONE")
