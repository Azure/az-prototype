"""
Test token exchange with each token source separately.
VS Code uses: Authorization: token <oauth_token>  +  X-GitHub-Api-Version: 2025-04-01
against: https://api.github.com/copilot_internal/v2/token
"""
import json
import sys
import os

# Add parent to path so we can use the project's auth code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import urllib.request
import urllib.error

from azext_prototype.ai.copilot_auth import (
    _read_keychain_token,
    _read_oauth_token,
    _read_gh_token,
)


def try_exchange(label, token):
    if not token:
        print(f"  [{label}] No token found")
        return None
    
    print(f"  [{label}] Token: {token[:15]}...")
    
    url = "https://api.github.com/copilot_internal/v2/token"
    
    # Exact VS Code headers
    headers = {
        "Authorization": f"token {token}",
        "X-GitHub-Api-Version": "2025-04-01",
    }
    
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            print(f"  [{label}] SUCCESS! Status {resp.status}")
            print(f"  Keys: {list(data.keys())}")
            if "endpoints" in data:
                print(f"  Endpoints: {json.dumps(data['endpoints'], indent=4)}")
            if "token" in data:
                jwt = data["token"]
                print(f"  JWT: {jwt[:60]}...")
                # Decode JWT payload
                try:
                    import base64
                    parts = jwt.split(".")
                    if len(parts) >= 2:
                        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                        decoded = json.loads(base64.b64decode(payload))
                        print(f"  JWT payload keys: {list(decoded.keys())}")
                        if "exp" in decoded:
                            import datetime
                            print(f"  Expires: {datetime.datetime.fromtimestamp(decoded['exp'])}")
                except Exception as e:
                    print(f"  JWT decode error: {e}")
            return data
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except:
            pass
        print(f"  [{label}] HTTP {e.code}: {body}")
    except Exception as e:
        print(f"  [{label}] Error: {e}")
    return None


def fetch_models(jwt_token, api_url):
    """Use the JWT to fetch models from the CAPI URL."""
    url = f"{api_url}/models"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "X-GitHub-Api-Version": "2025-04-01",
        "User-Agent": "GithubCopilot/1.300.0",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.37.5",
        "Copilot-Integration-Id": "vscode-chat",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            models = data.get("data", [])
            print(f"\n  Found {len(models)} models at {url}:")
            for m in models:
                mid = m.get("id", "?")
                family = m.get("capabilities", {}).get("family", "")
                picker = m.get("model_picker_enabled", "?")
                premium = m.get("billing", {}).get("is_premium", "?") if "billing" in m else "?"
                print(f"    {mid:40s} family={family:20s} picker={str(picker):5s} premium={premium}")
            return models
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except:
            pass
        print(f"  HTTP {e.code} from {url}: {body}")
    except Exception as e:
        print(f"  Error: {e}")
    return []


if __name__ == "__main__":
    print("=== Token Exchange Test (All Sources) ===\n")
    
    sources = [
        ("keychain", _read_keychain_token()),
        ("sdk-config", _read_oauth_token()),
        ("gh-cli", _read_gh_token()),
        ("env:COPILOT_GITHUB_TOKEN", os.environ.get("COPILOT_GITHUB_TOKEN", "").strip() or None),
        ("env:GH_TOKEN", os.environ.get("GH_TOKEN", "").strip() or None),
        ("env:GITHUB_TOKEN", os.environ.get("GITHUB_TOKEN", "").strip() or None),
    ]
    
    for label, token in sources:
        result = try_exchange(label, token)
        if result and "token" in result:
            jwt = result["token"]
            api_url = result.get("endpoints", {}).get("api", "https://api.githubcopilot.com")
            print(f"\n--- Fetching models using JWT from {label} ---")
            print(f"  API URL from token: {api_url}")
            fetch_models(jwt, api_url)
            break
    else:
        print("\nNo token source succeeded for exchange.")
