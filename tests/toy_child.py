"""A toy child web app for supervisor and proxy tests (stdlib only).

It behaves like the real children in the ways the launcher depends on: it serves under a
base path, requires an API token on ``api/*`` (except ``api/health``), prints a ready
line, and has an orderly shutdown route. Run as a script by ``tests/toy_adapter.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import socketserver
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    if handler.headers.get("transfer-encoding", "").lower() == "chunked":
        chunks = []
        while True:
            size = int(handler.rfile.readline().strip().split(b";")[0], 16)
            if size == 0:
                handler.rfile.readline()
                break
            chunks.append(handler.rfile.read(size))
            handler.rfile.readline()
        return b"".join(chunks)
    length = int(handler.headers.get("content-length") or 0)
    return handler.rfile.read(length) if length else b""


# The toy page and its embedded-guidance hook (ADR 0013, #77), like opnca-r's embed.js:
# it outlines its own marked controls and reports whether Send was pressed. It never
# answers for "toy.legacy.thing", like an app version without the hook.
STYLE = b"p { color: teal; } .ring { outline: 3px solid teal; }"
PAGE = b"""<!doctype html><link rel="stylesheet" href="style.css"><p>toy</p>
<button type="button" id="send" data-target="toy.form.send">Send</button>
<div hidden><button type="button" data-target="toy.form.hidden">Hidden</button></div>
<p id="sent"></p>
<script src="guide.js"></script>"""

GUIDE_JS = b"""(function () {
  var TARGETS = ["toy.form.send", "toy.form.hidden"];
  var sent = false, ringed = null;
  document.getElementById("send").addEventListener("click", function () {
    sent = true;
    document.getElementById("sent").textContent = "Sent";
  });
  function find(id) {
    var all = document.querySelectorAll("[data-target]");
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.getAttribute("data-target") === id && !el.closest("[hidden]")) return el;
    }
    return null;
  }
  function clear() { if (ringed) ringed.classList.remove("ring"); ringed = null; }
  window.toyGuidance = [];
  window.addEventListener("message", function (ev) {
    if (ev.origin !== location.origin || ev.source !== window.parent) return;
    var d = ev.data;
    if (!d || d.type !== "guidance" || d.v !== 1) return;
    window.toyGuidance.push(d);
    if (d.op === "clear") return clear();
    if (d.id === "toy.legacy.thing") return;
    var state;
    if (d.op === "highlight") {
      if (TARGETS.indexOf(d.id) < 0) { clear(); state = "unknown"; }
      else {
        var el = find(d.id);
        clear();
        if (el) { el.classList.add("ring"); ringed = el; state = "shown"; } else state = "absent";
      }
    } else if (d.op === "condition") {
      state = d.id === "toy.form.sent" ? sent : "unknown";
    } else return;
    ev.source.postMessage({type: "guidance-reply", v: 1, op: d.op, id: d.id, seq: d.seq,
                           state: state}, location.origin);
  });
})();"""


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind calls socket.getfqdn(), a reverse-DNS lookup that can
        # take minutes on CI macOS runners. The real children don't do this.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--base-path", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--grandchild", action="store_true")
    parser.add_argument("--fail-first", help="Exit 3 once if this marker file is missing.")
    parser.add_argument("--never-ready", action="store_true")
    parser.add_argument("--say", action="append", default=[], help="Print this line at start.")
    args = parser.parse_args()

    if args.fail_first and not Path(args.fail_first).exists():
        Path(args.fail_first).write_text("failed once")
        return 3

    token = os.environ[args.token_env]
    base = args.base_path
    port = args.port

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:  # keep test output quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra=()) -> None:
            self.send_response(status)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, doc: object, extra=()) -> None:
            self._send(status, json.dumps(doc).encode(), "application/json", extra)

        def _handle(self) -> None:
            path, _, query = self.path.partition("?")
            if not path.startswith(base):
                self._json(404, {"error": "outside base path", "path": path})
                return
            rel = path[len(base) :]
            needs_token = rel.startswith("api/") and rel != "api/health"
            if needs_token and self.headers.get("x-toy-token") != token:
                self._json(403, {"error": "token"})
                return
            body = _read_body(self)
            if rel == "":
                self._send(200, PAGE, "text/html")
            elif rel == "style.css":
                self._send(200, STYLE, "text/css")
            elif rel == "guide.js":
                self._send(200, GUIDE_JS, "text/javascript")
            elif rel == "api/health":
                if args.never_ready:
                    self._json(503, {"ok": False})
                else:
                    self._json(200, {"ok": True, "pid": os.getpid()})
            elif rel == "api/echo":
                self._json(
                    200,
                    {
                        "method": self.command,
                        "path": path,
                        "query": query,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body_len": len(body),
                    },
                )
            elif rel == "api/redirect-absolute":
                self._send(302, b"", "text/plain", [("location", f"http://127.0.0.1:{port}{base}")])
            elif rel == "api/redirect-relative":
                self._send(302, b"", "text/plain", [("location", "../style.css")])
            elif rel == "api/cookie":
                extra = [("set-cookie", "child=1; Path=/"), ("access-control-allow-origin", "*")]
                self._json(200, {"ok": True}, extra)
            elif rel == "api/shutdown" and self.command == "POST":
                self._json(202, {"stopping": True})
                threading.Thread(target=server.shutdown, daemon=True).start()
            elif rel == "api/crash":
                os._exit(9)
            else:
                self._json(404, {"error": "no route", "rel": rel})

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle

    server = _Server(("127.0.0.1", port), Handler)
    grandchild = None
    if args.grandchild:
        kwargs = {"start_new_session": True} if os.name != "nt" else {}
        grandchild = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", "import time; time.sleep(600)"], **kwargs
        ).pid
    ready = {"url": f"http://127.0.0.1:{port}{base}", "pid": os.getpid(), "grandchild": grandchild}
    for line in args.say:
        print(line, flush=True)
    print("TOY_READY " + json.dumps(ready), flush=True)
    print("toy env keys: " + ",".join(sorted(os.environ)), flush=True)
    server.serve_forever()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
