"""Command-line entry point: ``opncafyr launch | doctor | version``."""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path

from opncafyr import __version__
from opncafyr.core.instance import CLAIM_WAIT, claim, clear_record, write_record
from opncafyr.paths import app_root, state_dir

LOOPBACK = "127.0.0.1"


def _bind_loopback(port: int) -> socket.socket:
    """Bind the listener ourselves so the ephemeral port is known before the app is built."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOOPBACK, port))
    return sock


def _open_when_healthy(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}health", timeout=1) as resp:  # noqa: S310
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.2)
    print("Launcher did not become healthy; not opening a browser.", file=sys.stderr)


def run_controller(
    *,
    state: Path,
    port: int = 0,
    open_browser: bool = True,
    claim_wait: float = CLAIM_WAIT,
) -> int:
    """Start the controller, or reopen the one already running for this user."""
    import uvicorn

    from opncafyr.core.app import create_app
    from opncafyr.core.security import SecurityConfig

    claimed = claim(state, wait=claim_wait)
    if claimed.url is not None:
        print(f"opncafyr is already running at {claimed.url}", file=sys.stderr)
        if open_browser:
            webbrowser.open(claimed.url)
        return 0
    if claimed.lock is None:
        print(claimed.error, file=sys.stderr)
        return 1

    instance_id = uuid.uuid4().hex
    try:
        sock = _bind_loopback(port)
        port = sock.getsockname()[1]
        security = SecurityConfig(port=port)

        server: uvicorn.Server | None = None

        def request_shutdown() -> None:
            if server is not None:
                server.should_exit = True

        app = create_app(security, request_shutdown=request_shutdown, instance_id=instance_id)
        server = uvicorn.Server(uvicorn.Config(app, log_level="info"))

        url = f"http://{LOOPBACK}:{port}/"
        write_record(state, instance_id=instance_id, port=port)
        print(f"opncafyr {__version__} listening on {url}", file=sys.stderr)
        if open_browser:
            threading.Thread(target=_open_when_healthy, args=(url,), daemon=True).start()
        server.run(sockets=[sock])
    finally:
        clear_record(state, instance_id)
        claimed.lock.release()
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    return run_controller(state=state_dir(), port=args.port, open_browser=not args.no_browser)


def cmd_doctor(_: argparse.Namespace) -> int:
    print(f"opncafyr {__version__}")
    print(f"python   {sys.version.split()[0]}")
    print(f"app root {app_root()}")
    # The installed product uses app-owned runtimes from runtime/pins.toml, not PATH.
    # This report only shows what a development machine has available.
    for tool in ("uv", "Rscript", "quarto", "cargo", "soffice"):
        print(f"{tool:<8} {shutil.which(tool) or 'not found'}")
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opncafyr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    launch = sub.add_parser("launch", help="Start the local workspace controller.")
    launch.add_argument("--port", type=int, default=0, help="Loopback port (0 = ephemeral).")
    launch.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    launch.set_defaults(func=cmd_launch)

    doctor = sub.add_parser("doctor", help="Report paths and tool availability.")
    doctor.set_defaults(func=cmd_doctor)

    version = sub.add_parser("version", help="Print the version.")
    version.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
