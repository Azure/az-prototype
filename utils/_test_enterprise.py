"""
Test direct access to api.enterprise.githubcopilot.com.

The Copilot CLI logs show it uses api.enterprise.githubcopilot.com directly
with 'token authentication' and the keychain OAuth token. Let's try.
"""
import json
import sys
import os
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from azext_prototype.ai.copilot_auth import _read_keychain_token

token = _read_keychain_token()
if not token:
    print("No keychain token!"); sys.exit(1)
print(f"Token: {token[:15]}...")

BASE = "https://api.enterprise.githubcopilot.com"


def try_request(label, url, headers, method="GET", body=None):
    print(f"\n--- {label} ---")
    print(f"  {method} {url}")
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            print(f"  SUCCESS {resp.status}")
            return data
    except urllib.error.HTTPError as e:
        b = ""
        try: b = e.read().decode()[:300]
        except: pass
        print(f"  HTTP {e.code}: {b}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


# ---- STEP 1: Token exchange via enterprise endpoint ----
print("\n=== Step 1: Token Exchange ===")

exchange_configs = [
    (f"{BASE}/copilot_internal/v2/token", {
        "Authorization": f"token {token}",
        "X-GitHub-Api-Version": "2025-04-01",
        "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
        "Copilot-Integration-Id": "copilot-developer-cli",
    }),
    ("https://api.github.com/copilot_internal/v2/token", {
        "Authorization": f"token {token}",
        "X-GitHub-Api-Version": "2025-04-01",
        "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
        "Copilot-Integration-Id": "copilot-developer-cli",
    }),
]

jwt = None
for url, headers in exchange_configs:
    data = try_request("Token exchange", url, headers)
    if data and "token" in data:
        jwt = data["token"]
        print(f"  JWT: {jwt[:60]}...")
        if "endpoints" in data:
            print(f"  Endpoints: {json.dumps(data['endpoints'], indent=4)}")
        break


# ---- STEP 2: Try /models directly with OAuth token ----
print("\n=== Step 2: Models with OAuth token (no JWT) ===")

header_configs = [
    ("token auth + CLI headers", {
        "Authorization": f"token {token}",
        "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
        "Copilot-Integration-Id": "copilot-developer-cli",
        "Editor-Plugin-Version": "copilot/0.0.410",
        "Editor-Version": "copilot/0.0.410",
    }),
    ("Bearer + CLI headers", {
        "Authorization": f"Bearer {token}",
        "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
        "Copilot-Integration-Id": "copilot-developer-cli",
        "Editor-Plugin-Version": "copilot/0.0.410",
        "Editor-Version": "copilot/0.0.410",
    }),
    ("token auth minimal", {
        "Authorization": f"token {token}",
        "User-Agent": "copilot/0.0.410",
    }),
    ("Bearer minimal", {
        "Authorization": f"Bearer {token}",
        "User-Agent": "GithubCopilot/1.300.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.37.5",
    }),
]

for label, headers in header_configs:
    data = try_request(f"Models ({label})", f"{BASE}/models", headers)
    if data:
        models = data.get("data", [])
        print(f"  Found {len(models)} models:")
        for m in models[:5]:
            mid = m.get("id", "?")
            family = m.get("capabilities", {}).get("family", "")
            print(f"    {mid:40s} family={family}")
        if len(models) > 5:
            print(f"    ... and {len(models) - 5} more")
        
        # Try chat completions with Claude
        if models:
            claude_ids = [m["id"] for m in models if "claude" in m.get("id", "").lower()]
            test_model = claude_ids[0] if claude_ids else models[0]["id"]
            print(f"\n  Testing chat with {test_model}...")
            chat_body = json.dumps({
                "model": test_model,
                "messages": [{"role": "user", "content": "Say hello in 5 words."}],
                "max_tokens": 50,
                "stream": False,
            }).encode()
            chat_headers = dict(headers)
            chat_headers["Content-Type"] = "application/json"
            chat_headers["Openai-Intent"] = "conversation-panel"
            chat_data = try_request(f"Chat ({test_model})", f"{BASE}/chat/completions",
                                   chat_headers, method="POST", body=chat_body)
            if chat_data:
                content = chat_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(f"  Reply: {content}")
        break


# ---- STEP 3: If JWT obtained, try with JWT ----
if jwt:
    print("\n=== Step 3: Models with JWT ===")
    headers = {
        "Authorization": f"Bearer {jwt}",
        "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
        "Copilot-Integration-Id": "copilot-developer-cli",
    }
    data = try_request("Models (JWT)", f"{BASE}/models", headers)
    if data:
        models = data.get("data", [])
        print(f"  Found {len(models)} models")
