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
from opncafyr.core.lifecycle import (
    MAX_IDLE_MINUTES,
    MIN_IDLE_MINUTES,
    SETTINGS_FILE,
    LifecycleConfig,
    Settings,
    minutes_to_seconds,
)
from opncafyr.paths import app_root, logs_dir, state_dir

LOOPBACK = "127.0.0.1"
_SAVED = object()  # --idle-timeout not given: use the saved setting


def _loopback_host(value: str) -> str:
    """``--host`` exists only to refuse anything but loopback, loudly."""
    if value != LOOPBACK:
        raise argparse.ArgumentTypeError(
            f"opncafyr listens only on {LOOPBACK}; refusing {value!r}. Other hosts, "
            "addresses and interfaces could expose the launcher to other machines or users."
        )
    return value


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
    lifecycle: LifecycleConfig | None = None,
    log_dir: Path | None = None,
) -> int:
    """Start the controller, or reopen the one already running for this user.

    ``log_dir`` turns on the launcher log (``core.logs``); tests may leave it off.
    """
    import uvicorn

    from opncafyr.core.app import create_app
    from opncafyr.core.logs import configure_logging, reset_logging
    from opncafyr.core.registry import DescriptorError, load_descriptors
    from opncafyr.core.security import SecurityConfig

    try:
        tools = load_descriptors()
    except DescriptorError as exc:
        print(exc, file=sys.stderr)
        return 1

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
    if log_dir is not None:
        configure_logging(log_dir)
    try:
        sock = _bind_loopback(port)
        port = sock.getsockname()[1]
        security = SecurityConfig(port=port)

        server: uvicorn.Server | None = None

        def request_shutdown() -> None:
            if server is not None:
                server.should_exit = True

        app = create_app(
            security,
            request_shutdown=request_shutdown,
            instance_id=instance_id,
            lifecycle=lifecycle,
            settings=Settings(state / SETTINGS_FILE),
            tools=tools,
        )
        # log_config=None: uvicorn's loggers reach the root handlers set up above.
        server = uvicorn.Server(uvicorn.Config(app, log_level="info", log_config=None))

        url = f"http://{LOOPBACK}:{port}/"
        write_record(state, instance_id=instance_id, port=port)
        print(f"opncafyr {__version__} listening on {url}", file=sys.stderr)
        if open_browser:
            threading.Thread(target=_open_when_healthy, args=(url,), daemon=True).start()
        server.run(sockets=[sock])
    finally:
        clear_record(state, instance_id)
        claimed.lock.release()
        if log_dir is not None:
            reset_logging()
    return 0


def _idle_minutes(value: str) -> int | None:
    if value.lower() == "off":
        return None
    try:
        minutes = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use a number of minutes or 'off'") from None
    if not MIN_IDLE_MINUTES <= minutes <= MAX_IDLE_MINUTES:
        raise argparse.ArgumentTypeError(
            f"use {MIN_IDLE_MINUTES} to {MAX_IDLE_MINUTES} minutes, or 'off'"
        )
    return minutes


def cmd_launch(args: argparse.Namespace) -> int:
    lifecycle = None
    if args.idle_timeout is not _SAVED:
        lifecycle = LifecycleConfig(
            override_idle=True, idle_timeout_s=minutes_to_seconds(args.idle_timeout)
        )
    return run_controller(
        state=state_dir(),
        port=args.port,
        open_browser=not args.no_browser,
        lifecycle=lifecycle,
        log_dir=logs_dir(),
    )


def cmd_doctor(_: argparse.Namespace) -> int:
    from opncafyr.core.health import ToolHealth
    from opncafyr.core.registry import DescriptorError, load_descriptors
    from opncafyr.core.runtime import load_runtime

    print(f"opncafyr {__version__}")
    print(f"python   {sys.version.split()[0]}")
    print(f"app root {app_root()}")
    # The installed product uses app-owned runtimes from runtime/pins.toml, not PATH.
    # This part only shows what a development machine has available.
    for tool in ("uv", "Rscript", "quarto", "cargo", "soffice"):
        print(f"{tool:<8} {shutil.which(tool) or 'not found'}")
    try:
        tools = load_descriptors()
        runtime = load_runtime()
    except (DescriptorError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"runtime  {runtime.source or 'none installed'}")
    health = ToolHealth(tools, runtime)
    health.check_all()
    for tool_id in tools:
        status = health.status(tool_id)
        print(f"tool {tool_id:<10} {status.state}: {status.detail}")
        for req in status.missing:
            print(f"     {'':<10} {req.message}")
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opncafyr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    launch = sub.add_parser("launch", help="Start the local workspace controller.")
    launch.add_argument("--port", type=int, default=0, help="Loopback port (0 = ephemeral).")
    launch.add_argument(
        "--host",
        type=_loopback_host,
        default=LOOPBACK,
        help=f"Only {LOOPBACK} is accepted; anything else is refused.",
    )
    launch.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    launch.add_argument(
        "--idle-timeout",
        type=_idle_minutes,
        default=_SAVED,
        metavar="MINUTES|off",
        help="Stop after this many idle minutes, for this launch only "
        "(default: the saved setting, 30 minutes unless changed).",
    )
    launch.set_defaults(func=cmd_launch)

    doctor = sub.add_parser("doctor", help="Report paths and tool availability.")
    doctor.set_defaults(func=cmd_doctor)

    version = sub.add_parser("version", help="Print the version.")
    version.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
