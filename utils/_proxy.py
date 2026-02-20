"""
Proxy to intercept Copilot CLI traffic.

Starts a local HTTPS proxy that intercepts requests to api.github.com
and logs them, then forwards them. This shows us exactly what the
Copilot CLI sends for token exchange.

Usage:
  1. Run this script
  2. In another terminal: set HTTPS_PROXY=http://127.0.0.1:8899 && copilot.exe ...
"""
import http.server
import json
import ssl
import sys
import urllib.request
import urllib.error
from urllib.parse import urlparse


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def _proxy(self, method):
        # Log the request
        print(f"\n{'='*60}")
        print(f">>> {method} {self.path}")
        print(f">>> Headers:")
        for k, v in self.headers.items():
            # Mask token values partially
            if "auth" in k.lower() and len(v) > 20:
                print(f"    {k}: {v[:25]}...{v[-5:]}")
            else:
                print(f"    {k}: {v}")

        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            if length:
                body = self.rfile.read(length)
                try:
                    print(f">>> Body: {json.dumps(json.loads(body), indent=2)[:500]}")
                except:
                    print(f">>> Body: {body[:200]}")

        # Forward the request
        parsed = urlparse(self.path)
        url = self.path
        if not url.startswith("http"):
            url = f"https://{self.headers.get('Host', 'api.github.com')}{self.path}"

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "proxy-connection", "proxy-authorization")}

        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                print(f"\n<<< {resp.status} {resp.reason}")
                try:
                    data = json.loads(resp_body)
                    # Truncate tokens in response
                    resp_str = json.dumps(data, indent=2)
                    if len(resp_str) > 2000:
                        print(f"<<< Body (truncated): {resp_str[:2000]}...")
                    else:
                        print(f"<<< Body: {resp_str}")
                except:
                    print(f"<<< Body: {resp_body[:500]}")

                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode()
            except:
                pass
            print(f"\n<<< {e.code}: {body_text[:500]}")
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(body_text.encode())
        except Exception as e:
            print(f"\n<<< ERROR: {e}")
            self.send_response(502)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging


if __name__ == "__main__":
    port = 8899
    print(f"Starting proxy on :{port}")
    print(f"Set HTTPS_PROXY=http://127.0.0.1:{port}")
    server = http.server.HTTPServer(("127.0.0.1", port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
