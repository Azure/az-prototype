"""Test token exchange with Copilot CLI integration ID."""
import json
import sys
import os
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from azext_prototype.ai.copilot_auth import _read_keychain_token

token = _read_keychain_token()
if not token:
    print("No keychain token found!")
    sys.exit(1)
print(f"Token: {token[:15]}...")

# Three header configurations to try
configs = [
    # Config 1: Copilot CLI exact headers
    {
        "Authorization": f"token {token}",
        "X-GitHub-Api-Version": "2025-04-01",
        "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
        "Copilot-Integration-Id": "copilot-developer-cli",
        "Editor-Plugin-Version": "copilot/0.0.410",
        "Editor-Version": "copilot/0.0.410",
    },
    # Config 2: Minimal with just version header
    {
        "Authorization": f"token {token}",
        "X-GitHub-Api-Version": "2025-04-01",
    },
    # Config 3: Just token
    {
        "Authorization": f"token {token}",
    },
]

url = "https://api.github.com/copilot_internal/v2/token"

for i, headers in enumerate(configs):
    print(f"\n--- Config {i+1} ---")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            print(f"  SUCCESS! Status: {resp.status}")
            print(f"  Keys: {list(data.keys())}")
            if "endpoints" in data:
                ep = data["endpoints"]
                print(f"  Endpoints: {json.dumps(ep, indent=4)}")
            if "token" in data:
                print(f"  JWT token: {data['token'][:60]}...")
            if "expires_at" in data:
                print(f"  Expires: {data['expires_at']}")
            
            # Now fetch models
            jwt = data.get("token", "")
            api_url = data.get("endpoints", {}).get("api", "https://api.githubcopilot.com")
            models_url = f"{api_url}/models"
            print(f"\n  Fetching models from: {models_url}")
            
            mheaders = {
                "Authorization": f"Bearer {jwt}",
                "User-Agent": "copilot/0.0.410 (win32 v24.11.1) term/unknown",
                "Copilot-Integration-Id": "copilot-developer-cli",
                "Editor-Plugin-Version": "copilot/0.0.410",
                "Editor-Version": "copilot/0.0.410",
                "Openai-Intent": "conversation-panel",
            }
            req2 = urllib.request.Request(models_url, headers=mheaders)
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                mdata = json.loads(resp2.read())
                models = mdata.get("data", [])
                print(f"  Found {len(models)} models:")
                for m in models:
                    mid = m.get("id", "?")
                    family = m.get("capabilities", {}).get("family", "")
                    picker = m.get("model_picker_enabled", False)
                    print(f"    {mid:42s} family={family:22s} picker={picker}")
            break
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except:
            pass
        print(f"  HTTP {e.code}: {body}")
    except Exception as e:
        print(f"  Error: {e}")
