"""
Replicate VS Code Copilot Chat's token exchange flow.

VS Code does:
1. Gets OAuth token from keychain (copilot login)
2. Exchanges it via POST api.github.com/copilot_internal/v2/token
3. Gets back a JWT with endpoints.api -> actual CAPI URL
4. Uses that URL for /models and /chat/completions
"""
import json
import subprocess
import sys

import urllib.request
import urllib.error

# Get the keychain token (copilot login token)
def get_keychain_token():
    """Try multiple sources for the copilot OAuth token."""
    import platform
    if platform.system() == "Windows":
        try:
            ps = r'''
            Add-Type -AssemblyName System.Security
            $path = "$env:LOCALAPPDATA\github-copilot\hosts.json"
            if (Test-Path $path) {
                $j = Get-Content $path -Raw | ConvertFrom-Json
                foreach ($p in $j.PSObject.Properties) {
                    if ($p.Value.oauth_token) { Write-Output $p.Value.oauth_token; break }
                }
            }
            '''
            r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True, timeout=10)
            tok = r.stdout.strip()
            if tok:
                return tok, "keychain"
        except Exception as e:
            print(f"  Keychain error: {e}")
    
    # Also try gh auth token
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        tok = r.stdout.strip()
        if tok:
            return tok, "gh-cli"
    except Exception:
        pass
    
    return None, None


def exchange_token(oauth_token):
    """Exchange OAuth token for Copilot API token (JWT) via copilot_internal/v2/token."""
    
    # VS Code extension headers from the extension.js analysis  
    # VS Code sends EXACTLY these two headers - nothing else
    headers_sets = [
        # Set 1: Exact VS Code headers (token format + API version)
        {
            "Authorization": f"token {oauth_token}",
            "X-GitHub-Api-Version": "2025-04-01",
        },
        # Set 2: With Accept header
        {
            "Authorization": f"token {oauth_token}",
            "X-GitHub-Api-Version": "2025-04-01",
            "Accept": "application/json",
        },
        # Set 3: Bearer variant
        {
            "Authorization": f"Bearer {oauth_token}",
            "X-GitHub-Api-Version": "2025-04-01",
        },
    ]
    
    urls = [
        "https://api.github.com/copilot_internal/v2/token",
    ]
    
    for url in urls:
        for i, headers in enumerate(headers_sets):
            try:
                req = urllib.request.Request(url, method="GET", headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                    print(f"\n[SUCCESS] {url} with header set {i+1}")
                    print(f"  Status: {resp.status}")
                    print(f"  Keys: {list(data.keys())}")
                    if "endpoints" in data:
                        print(f"  Endpoints: {json.dumps(data['endpoints'], indent=2)}")
                    if "token" in data:
                        tok = data["token"]
                        print(f"  Token: {tok[:50]}...")
                    if "expires_at" in data:
                        print(f"  Expires: {data['expires_at']}")
                    return data
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode()[:200]
                except:
                    pass
                print(f"  [{e.code}] {url} (headers {i+1}): {body}")
            except Exception as e:
                print(f"  [ERR] {url} (headers {i+1}): {e}")
    
    return None


def test_models_with_jwt(jwt_token, api_url):
    """Use the JWT token to query models from the provided API URL."""
    models_url = f"{api_url}/models"
    print(f"\n--- Fetching models from {models_url} ---")
    
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/json",
        "User-Agent": "GithubCopilot/1.300.0",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.37.5",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-panel",
    }
    
    try:
        req = urllib.request.Request(models_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            models = data.get("data", data.get("models", []))
            print(f"  Found {len(models)} models:")
            for m in models:
                name = m.get("id") or m.get("name", "?")
                family = m.get("capabilities", {}).get("family", "")
                picker = m.get("model_picker_enabled", "?")
                print(f"    {name:40s} family={family:20s} picker={picker}")
            return models
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except:
            pass
        print(f"  HTTP {e.code}: {body}")
    except Exception as e:
        print(f"  Error: {e}")
    return []


def test_chat_with_jwt(jwt_token, api_url, model="claude-sonnet-4"):
    """Try a chat completion with the JWT against the CAPI URL."""
    chat_url = f"{api_url}/chat/completions"
    print(f"\n--- Chat test: {model} via {chat_url} ---")
    
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Say hello in 5 words."}],
        "max_tokens": 50,
        "stream": False,
    }).encode()
    
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "GithubCopilot/1.300.0",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.37.5",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-panel",
    }
    
    try:
        req = urllib.request.Request(chat_url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"  [OK] {model}: {content}")
            return True
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except:
            pass
        print(f"  HTTP {e.code}: {body}")
    except Exception as e:
        print(f"  Error: {e}")
    return False


if __name__ == "__main__":
    print("=== Copilot Token Exchange Test ===\n")
    
    token, source = get_keychain_token()
    if not token:
        print("No token found!")
        sys.exit(1)
    
    print(f"Using {source} token: {token[:12]}...")
    
    print("\n--- Step 1: Token Exchange ---")
    result = exchange_token(token)
    
    if not result:
        print("\nToken exchange failed with all methods.")
        sys.exit(1)
    
    jwt_token = result.get("token", "")
    endpoints = result.get("endpoints", {})
    api_url = endpoints.get("api", "https://api.githubcopilot.com")
    
    print(f"\n--- Step 2: API URL = {api_url} ---")
    
    print("\n--- Step 3: Fetch Models ---")
    models = test_models_with_jwt(jwt_token, api_url)
    
    if models:
        print(f"\n--- Step 4: Test Claude ---")
        claude_models = [m.get("id") for m in models if "claude" in (m.get("id") or "").lower()]
        if claude_models:
            for cm in claude_models[:3]:
                test_chat_with_jwt(jwt_token, api_url, cm)
        else:
            print("  No Claude models in list, testing gpt-4o...")
            test_chat_with_jwt(jwt_token, api_url, "gpt-4o")
