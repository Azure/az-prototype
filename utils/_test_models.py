"""Test which models are available on the Copilot completions API."""
import subprocess, requests, json, sys, shutil

GH = shutil.which("gh")
token = subprocess.run([GH, "auth", "token"], capture_output=True, text=True).stdout.strip()

URL = "https://api.githubcopilot.com/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GithubCopilot/1.300.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.100.0",
    "Editor-Plugin-Version": "copilot-chat/0.37.5",
}

# Models currently listed in our code
MODELS = [
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "gpt-4o",
    "gpt-4.1",
    "o3-mini",
    "gemini-2.5-pro",
    # Additional models to probe
    "claude-opus-4.5",
    "claude-haiku-4.5",
    "gpt-4o-mini",
    "claude-3.5-sonnet",
    "claude-sonnet-3.5",
    "claude-3.7-sonnet",
    "claude-sonnet-3.7",
    "claude-sonnet-4.0",
    "claude-4-sonnet",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o3",
    "o4-mini",
]

payload_template = {
    "messages": [{"role": "user", "content": "Say hello in one word."}],
    "temperature": 0.0,
    "max_tokens": 10,
}

print(f"Testing {len(MODELS)} models against {URL}\n")
print(f"{'Model':<30} {'Status':<8} {'Detail'}")
print("-" * 80)

for model in MODELS:
    payload = {**payload_template, "model": model}
    try:
        resp = requests.post(URL, headers=HEADERS, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"{model:<30} {'OK':<8} response: {content[:50]}")
        else:
            try:
                err = resp.json()
                msg = err.get("error", {}).get("message", resp.text[:100])
            except Exception:
                msg = resp.text[:100]
            print(f"{model:<30} {resp.status_code:<8} {msg}")
    except Exception as e:
        print(f"{model:<30} {'ERROR':<8} {e}")
