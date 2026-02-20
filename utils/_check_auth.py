"""Quick check: gh auth status + try token exchange."""
import shutil, subprocess, requests, json, sys

GH = shutil.which("gh")
if not GH:
    print("ERROR: gh CLI not found on PATH")
    sys.exit(1)

# 1) gh auth status
r = subprocess.run([GH, "auth", "status"], capture_output=True, text=True)
print("=== gh auth status ===")
print(r.stdout)
print(r.stderr)

# 2) Get current gh token
r2 = subprocess.run([GH, "auth", "token"], capture_output=True, text=True)
token = r2.stdout.strip()
print(f"\n=== gh auth token === (prefix: {token[:12]}...)")

# 3) Try token exchange via direct HTTP
print("\n=== Token exchange via HTTP ===")
url = "https://api.github.com/copilot_internal/v2/token"
for fmt in ["token", "Bearer"]:
    hdr = {"Authorization": f"{fmt} {token}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=hdr, timeout=10)
        print(f"  {fmt} format: {resp.status_code} => {resp.text[:200]}")
    except Exception as e:
        print(f"  {fmt} format: ERROR {e}")

# 4) Try token exchange via gh api
print("\n=== Token exchange via gh api ===")
r3 = subprocess.run([GH, "api", "copilot_internal/v2/token"], capture_output=True, text=True)
print(f"  stdout: {r3.stdout[:300]}")
print(f"  stderr: {r3.stderr[:300]}")
print(f"  returncode: {r3.returncode}")
