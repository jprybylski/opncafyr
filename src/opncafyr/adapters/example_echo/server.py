"""The example_echo toy web app (#41): a tiny page and an echo API. Standard library only.

It does what the launcher needs from any child web app (docs/adding-a-tool.md):
- binds to ``127.0.0.1`` on the port it is given;
- serves everything under the base path (``/apps/example_echo/``), unchanged by the proxy;
- refuses a foreign ``Host`` and cross-site requests, so another site can't drive its port;
- requires the per-launch token header on every route except ``api/health`` (the proxy
  adds it to every request it forwards);
- prints a ready line once it is listening;
- stops cleanly on ``POST api/shutdown``.

Usage: ``python -I server.py --port N --base-path /apps/example_echo/ --token-env NAME``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

READY_PREFIX = "ECHO_READY "
TOKEN_HEADER = "x-example-echo-token"  # noqa: S105 - a header name, not a secret
MAX_BODY = 1 << 20

PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Example echo</title>
<link rel="stylesheet" href="style.css"></head>
<body>
<h1>Example echo</h1>
<p>A toy tool that shows how a web app runs inside opncafyr. It is not part of a release.</p>
<p>Its API answers at <code>api/echo</code>.</p>
</body>
</html>
"""
STYLE = "body { font-family: system-ui, sans-serif; margin: 2rem; } code { color: teal; }\n"


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # Skip HTTPServer's reverse-DNS lookup (socket.getfqdn), which can be slow.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def make_handler(base: str, token: str, port: int, server_ref: list[ThreadingHTTPServer]):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # no request lines in the child log
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, doc: object) -> None:
            self._send(status, json.dumps(doc).encode(), "application/json")

        def _handle(self) -> None:
            path = self.path.partition("?")[0]
            if not path.startswith(base):
                self._json(404, {"error": "outside the base path"})
                return
            rel = path[len(base) :]
            if self.headers.get("host") not in hosts:
                self._json(403, {"error": "unexpected Host"})
                return
            if self.headers.get("sec-fetch-site", "none") not in {"none", "same-origin"}:
                self._json(403, {"error": "cross-site request"})
                return
            needs_token = rel != "api/health"
            if needs_token and not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), token):
                self._json(403, {"error": "missing or wrong token"})
                return
            try:
                length = int(self.headers.get("content-length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._json(400, {"error": "bad Content-Length"})
                return
            if length > MAX_BODY:
                self._json(413, {"error": "body too large"})
                return
            body = self.rfile.read(length) if length else b""
            if rel == "" and self.command in {"GET", "HEAD"}:
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif rel == "style.css":
                self._send(200, STYLE.encode(), "text/css")
            elif rel == "api/health":
                self._json(200, {"ok": True})
            elif rel == "api/echo":
                # Echo only the shape of the request, never its body.
                self._json(200, {"method": self.command, "path": path, "body_bytes": len(body)})
            elif rel == "api/shutdown" and self.command == "POST":
                self._json(202, {"stopping": True})
                threading.Thread(target=server_ref[0].shutdown, daemon=True).start()
            else:
                self._json(404, {"error": "no such route"})

        do_GET = do_POST = do_HEAD = _handle

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--base-path", required=True)
    parser.add_argument("--token-env", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"example_echo: {args.token_env} is not set", file=sys.stderr)
        return 2
    ref: list[ThreadingHTTPServer] = []
    handler = make_handler(args.base_path, token, args.port, ref)
    server = _Server(("127.0.0.1", args.port), handler)
    ref.append(server)
    print(READY_PREFIX + json.dumps({"port": args.port}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
