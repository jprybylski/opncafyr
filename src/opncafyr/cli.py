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
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from opncafyr import __version__
from opncafyr.core.bootstrap import mint_open_token, open_url, tokens_dir
from opncafyr.core.instance import CLAIM_WAIT, claim, clear_record, write_record
from opncafyr.core.lifecycle import (
    MAX_IDLE_MINUTES,
    MIN_IDLE_MINUTES,
    SETTINGS_FILE,
    LifecycleConfig,
    Settings,
    minutes_to_seconds,
)
from opncafyr.core.provenance import IMAGE_ID_RE
from opncafyr.core.remote import (
    ACCESS_CHOICES,
    ACCESS_SSH,
    RemoteAccess,
    RemoteAccessError,
    container_access,
    native_access,
)
from opncafyr.paths import app_root, logs_dir, state_dir

if TYPE_CHECKING:
    from opncafyr.core.runtime import Runtime

LOOPBACK = "127.0.0.1"
GIT_VERSION_TIMEOUT = 30  # seconds, for `doctor`'s `git --version`
_SAVED = object()  # --idle-timeout not given: use the saved setting


def _loopback_host(value: str) -> str:
    """``--host`` exists only to refuse anything but loopback, loudly."""
    if value != LOOPBACK:
        raise argparse.ArgumentTypeError(
            f"opncafyr listens only on {LOOPBACK}; refusing {value!r}. Other hosts, "
            "addresses and interfaces could expose the launcher to other machines or users."
        )
    return value


def _bind_loopback(port: int, host: str = LOOPBACK) -> socket.socket:
    """Bind the listener ourselves so the ephemeral port is known before the app is built.

    ``host`` is the loopback address, except in cloud container mode (``core.remote``),
    where the container interface is bound and Docker publishes it to the VM's loopback.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


def _open_when_healthy(
    url: str, timeout: float = 15.0, target: Callable[[], str] | None = None
) -> None:
    """Open the browser once ``url`` answers ``/health``.

    ``target`` makes the URL to open (a fresh one-time ``/open`` link) only at that moment,
    so the token's lifetime starts when the browser is about to use it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}health", timeout=1) as resp:  # noqa: S310
                if resp.status == 200:
                    webbrowser.open(target() if target is not None else url)
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
    remote: RemoteAccess | None = None,
    workspace: Path | None = None,
    container_image: str | None = None,
    print_url: bool = False,
) -> int:
    """Start the controller, or reopen the one already running for this user.

    ``log_dir`` turns on the launcher log (``core.logs``); tests may leave it off.
    ``remote`` is cloud container mode (#42): no browser is opened; the one-time link is
    printed with the public URL instead. ``print_url`` only prints a fresh link for the
    instance already running (used to reattach), and starts nothing.
    """
    import uvicorn

    from opncafyr.core.app import create_app
    from opncafyr.core.logs import configure_logging, reset_logging
    from opncafyr.core.registry import DescriptorError, load_descriptors
    from opncafyr.core.security import SecurityConfig
    from opncafyr.core.storage import owner_mismatch

    try:
        tools = load_descriptors()
    except DescriptorError as exc:
        print(exc, file=sys.stderr)
        return 1

    tokens = tokens_dir(state)
    claimed = claim(state, wait=claim_wait)
    if claimed.url is not None:
        if remote is not None or print_url:
            public = remote.public_url if remote is not None else claimed.url
            print(open_url(public, mint_open_token(tokens)))
            print(_link_note(remote), file=sys.stderr)
            return 0
        print(f"opncafyr is already running at {claimed.url}", file=sys.stderr)
        if open_browser:
            webbrowser.open(open_url(claimed.url, mint_open_token(tokens)))
        return 0
    if print_url:
        if claimed.lock is not None:
            claimed.lock.release()
        print("opncafyr is not running.", file=sys.stderr)
        return 1
    if claimed.lock is None:
        print(claimed.error, file=sys.stderr)
        return 1

    instance_id = uuid.uuid4().hex
    if log_dir is not None:
        configure_logging(log_dir)
    try:
        sock = _bind_loopback(port, remote.bind if remote is not None else LOOPBACK)
        port = sock.getsockname()[1]
        if remote is None:
            security = SecurityConfig(port=port)
        else:
            security = SecurityConfig(
                port=port,
                extra_hosts=remote.hosts,
                extra_origins=remote.origins,
                base_path=remote.base_path,
                cookie_id=remote.cookie_id,
                secure_cookie=remote.secure_cookie,
            )

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
            open_tokens=tokens,
            workspace=workspace,
            container_image=container_image,
        )
        # log_config=None: uvicorn's loggers reach the root handlers set up above.
        server = uvicorn.Server(uvicorn.Config(app, log_level="info", log_config=None))

        url = f"http://{LOOPBACK}:{port}/"
        write_record(state, instance_id=instance_id, port=port)
        if remote is not None:
            mode = "remote, on this machine's loopback" if remote.native else "container"
            print(f"opncafyr {__version__} listening on port {port} ({mode})", file=sys.stderr)
            mismatch = (
                owner_mismatch(workspace, container=not remote.native)
                if workspace is not None
                else None
            )
            if mismatch:
                print(f"Warning: {mismatch}", file=sys.stderr)
            threading.Thread(
                target=_print_when_healthy,
                args=(url, remote, lambda: open_url(remote.public_url, mint_open_token(tokens))),
                daemon=True,
            ).start()
            server.run(sockets=[sock])
            return 0
        print(f"opncafyr {__version__} listening on {url}", file=sys.stderr)
        if not open_browser:
            print(
                "Run `opncafyr launch` again to open it in a browser "
                "(the page needs a one-time link).",
                file=sys.stderr,
            )
        else:
            threading.Thread(
                target=_open_when_healthy,
                args=(url,),
                kwargs={"target": lambda: open_url(url, mint_open_token(tokens))},
                daemon=True,
            ).start()
        server.run(sockets=[sock])
    finally:
        clear_record(state, instance_id)
        claimed.lock.release()
        if log_dir is not None:
            reset_logging()
    return 0


def _link_note(remote: RemoteAccess | None = None) -> str:
    if remote is not None and remote.native:
        return (
            "This link works once, within 2 minutes. For a new one run the same "
            "`opncafyr launch --remote …` command again on the VM."
        )
    return (
        "This link works once, within 2 minutes. For a new one run "
        "`opncafyr-container url <study folder>` on the VM."
    )


def _print_when_healthy(url: str, remote: RemoteAccess, link: Callable[[], str]) -> None:
    """Container mode: print the one-time link once the controller answers."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}health", timeout=1) as resp:  # noqa: S310
                if resp.status == 200:
                    print(f"Open: {link()}", file=sys.stderr, flush=True)
                    print(_link_note(remote), file=sys.stderr, flush=True)
                    return
        except OSError:
            time.sleep(0.2)
    print("Launcher did not become healthy.", file=sys.stderr)


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


REMOTE_OPTIONS = (
    "access",
    "public_url",
    "local_port",
    "allowed_host",
    "allowed_origin",
    "workspace",
    "container_image",
)


def cmd_launch(args: argparse.Namespace) -> int:
    lifecycle = None
    if args.idle_timeout is not _SAVED:
        lifecycle = LifecycleConfig(
            override_idle=True, idle_timeout_s=minutes_to_seconds(args.idle_timeout)
        )
    remote = None
    workspace = None
    if args.container and args.remote:
        print("--container and --remote are different modes; use one.", file=sys.stderr)
        return 2
    if args.container or args.remote:
        mode = "--container" if args.container else "--remote"
        if not args.port:
            print(f"{mode} needs a fixed --port.", file=sys.stderr)
            return 2
        if args.remote and not args.workspace:
            print(
                "--remote needs --workspace: there is no folder picker on a headless VM.",
                file=sys.stderr,
            )
            return 2
        if args.remote and args.container_image is not None:
            print("--container-image is only for --container.", file=sys.stderr)
            return 2
        make_access = container_access if args.container else native_access
        try:
            remote = make_access(
                port=args.port,
                access=args.access or ACCESS_SSH,
                public_url=args.public_url,
                local_port=args.local_port,
                allowed_hosts=tuple(args.allowed_host or ()),
                allowed_origins=tuple(args.allowed_origin or ()),
            )
        except RemoteAccessError as exc:
            print(exc, file=sys.stderr)
            return 2
        if args.container_image is not None and not IMAGE_ID_RE.match(args.container_image):
            print("--container-image must be an image ID (sha256:<64 hex>).", file=sys.stderr)
            return 2
        if args.workspace is not None:
            workspace = Path(args.workspace)
            if not workspace.is_dir():
                print(f"--workspace {workspace} is not a folder.", file=sys.stderr)
                return 2
    else:
        given = [o for o in REMOTE_OPTIONS if getattr(args, o) not in (None, [])]
        if given:
            flags = ", ".join("--" + o.replace("_", "-") for o in given)
            print(
                f"{flags}: only with --container (cloud container mode) or --remote "
                "(a native Linux VM).",
                file=sys.stderr,
            )
            return 2
    return run_controller(
        state=state_dir(),
        port=args.port,
        open_browser=not args.no_browser and remote is None,
        lifecycle=lifecycle,
        log_dir=logs_dir(),
        remote=remote,
        workspace=workspace,
        container_image=args.container_image,
        print_url=args.print_url,
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    from opncafyr.core.health import ToolHealth
    from opncafyr.core.registry import DescriptorError, load_descriptors
    from opncafyr.core.runtime import load_runtime

    if args.bundle or args.preview:
        return _support_bundle(args)
    print(f"opncafyr {__version__}")
    print(f"python   {sys.version.split()[0]}")
    print(f"app root {app_root()}")
    from opncafyr.paths import active_install_dir

    install = active_install_dir()
    print(f"install  {install.name if install else 'not running from an install'}")
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
    print(f"git      {_trusted_git(runtime)}")
    health = ToolHealth(tools, runtime)
    health.check_all()
    for tool_id in tools:
        status = health.status(tool_id)
        print(f"tool {tool_id:<10} {status.state}: {status.detail}")
        for req in status.missing:
            print(f"     {'':<10} {req.message}")
    return 0


def _trusted_git(runtime: Runtime) -> str:
    """The pinned Git from the runtime record (#56), and the version it reports.

    Only this Git is trusted for version history; a ``git`` on ``PATH`` never is.
    """
    from opncafyr.core.proctree import DEVNULL, PIPE, TimeoutExpired, child_env, spawn
    from opncafyr.installer.steps.git import EXECUTABLE, exec_path

    if not runtime.has(EXECUTABLE):
        return "not installed (version history unavailable)"
    git = runtime.executable(EXECUTABLE)
    argv = [git, f"--exec-path={exec_path(git)}", "--version"]
    env = child_env({"GIT_CONFIG_NOSYSTEM": "1"})
    try:
        proc = spawn(argv, env=env, cwd=git.parent, stdout=PIPE, stderr=DEVNULL)
    except OSError as exc:
        return f"{git} (does not start: {type(exc).__name__})"
    try:
        out, _ = proc.popen.communicate(timeout=GIT_VERSION_TIMEOUT)
    except TimeoutExpired:
        proc.kill_tree()
        return f"{git} (no answer within {GIT_VERSION_TIMEOUT} s)"
    if proc.popen.returncode != 0:
        return f"{git} (exit code {proc.popen.returncode})"
    return f"{git} ({out.decode(errors='replace').strip()[:80]})"


def _support_bundle(args: argparse.Namespace) -> int:
    """``doctor --preview`` / ``doctor --bundle``: the redacted support bundle (#38)."""
    from opncafyr.core import support
    from opncafyr.core.health import ToolHealth
    from opncafyr.core.registry import DescriptorError, load_descriptors, load_workflows
    from opncafyr.core.runtime import load_runtime

    workspace = None
    if args.workspace:
        workspace = Path(args.workspace).resolve()
        if not (workspace / ".opncafyr").is_dir():
            print(f"{workspace.name} is not a study folder (no .opncafyr/).", file=sys.stderr)
            return 2
    try:
        tools = load_descriptors()
        runtime = load_runtime()
    except (DescriptorError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    health = ToolHealth(tools, runtime)
    health.check_all()
    entries = support.build(
        app_root=app_root(),
        tools=tools,
        health=health,
        runtime=runtime,
        workspace=workspace,
        sensitive=support.sensitive_values(load_workflows(), workspace),
    )
    if args.preview:
        for entry in entries:
            print(f"===== {entry.name} =====")
            print(entry.text, end="" if entry.text.endswith("\n") else "\n")
        print("(preview only: nothing was saved)")
        return 0
    print("The support bundle will contain:")
    for entry in entries:
        print(f"  {entry.name:<34} {len(entry.text.encode('utf-8')):>8} bytes")
    print("See every entry with `opncafyr doctor --preview`.")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Not saved: pass --yes to save without asking.", file=sys.stderr)
            return 2
        if input("Save it on this computer? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Not saved.")
            return 1
    path = support.write(entries, app_root())
    print(f"Saved {path}")
    print("It stays on this computer. Send it to support yourself if you choose to.")
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    """``opncafyr bundle``: the installer's bundle builder, from this install's payload."""
    from opncafyr.installer import cli as installer_cli
    from opncafyr.paths import active_install_dir

    install = active_install_dir()
    payload = install / "installer" if install is not None else None
    if payload is None or not (payload / "payload.json").is_file():
        print(
            "opncafyr bundle runs from an installed opncafyr. From a downloaded installer, "
            "run `install.sh bundle` or `install.bat bundle` instead.",
            file=sys.stderr,
        )
        return 1
    return installer_cli.main(["bundle", *args.bundle_args], payload_dir=payload)


def cmd_runs_verify(args: argparse.Namespace) -> int:
    """``opncafyr runs verify``: recompute every run's hashes and report any drift."""
    from opncafyr.core.runs import RunError, run_dir, runs_root, verify_run

    root = Path(args.workspace).resolve()
    if not (root / ".opncafyr").is_dir():
        print(f"{root} is not a study folder (no .opncafyr/).", file=sys.stderr)
        return 2
    try:
        if args.run:
            folders = [run_dir(root, args.run)]
        else:
            base = runs_root(root)
            folders = (
                sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
                if base.is_dir()
                else []
            )
    except RunError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    drift = False
    for folder in folders:
        report = verify_run(folder)
        if report["ok"]:
            how = "sealed, every file matches" if report["sealed"] else "named files match"
            print(f"{report['run_id']}  ok        {report.get('state', '')}: {how}")
            continue
        drift = True
        print(f"{report['run_id']}  DRIFT     {report.get('state', '')}")
        if "error" in report:
            print(f"    {report['error']}")
        for kind in ("changed", "missing", "added"):
            for path in report[kind]:
                print(f"    {kind:<8} {path}")
    if not folders:
        print("No runs.")
    return 1 if drift else 0


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
        "--print-url",
        action="store_true",
        help="Print a fresh one-time link for the running instance instead of starting one.",
    )
    remote = launch.add_argument_group(
        "cloud container mode (#42) and native Linux VMs (#65)",
        "--container: only inside the opncafyr container, started by opncafyr-container. "
        "--remote: on a native Linux VM. These only add the SSH forward's or IDE proxy's own "
        "host and origin; every security check stays.",
    )
    remote.add_argument(
        "--container",
        action="store_true",
        help="Listen on the container interface (published to the VM's 127.0.0.1 only).",
    )
    remote.add_argument(
        "--remote",
        action="store_true",
        help="A native Linux VM (#65), no container: listen on the VM's 127.0.0.1 only and "
        "print the one-time link; needs --port and --workspace.",
    )
    remote.add_argument("--access", choices=ACCESS_CHOICES, help="How the browser reaches it.")
    remote.add_argument("--public-url", help="The IDE proxy's URL for the port.")
    remote.add_argument("--local-port", type=int, help="ssh: the port on your own machine.")
    remote.add_argument("--allowed-host", action="append", metavar="HOST[:PORT]")
    remote.add_argument("--allowed-origin", action="append", metavar="SCHEME://HOST[:PORT]")
    remote.add_argument("--workspace", help="The one study folder to serve.")
    remote.add_argument("--container-image", help="The image ID, for provenance.")
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
    what = doctor.add_mutually_exclusive_group()
    what.add_argument(
        "--bundle",
        action="store_true",
        help="Save a redacted support bundle in <app root>/logs/support (asks first).",
    )
    what.add_argument(
        "--preview", action="store_true", help="Print the support bundle; save nothing."
    )
    doctor.add_argument(
        "--workspace", metavar="DIR", help="Include this study folder's job states and counts."
    )
    doctor.add_argument("--yes", action="store_true", help="With --bundle: save without asking.")
    doctor.set_defaults(func=cmd_doctor)

    bundle = sub.add_parser(
        "bundle",
        help="Build an offline bundle (same options as `install.sh bundle`).",
        add_help=False,
    )
    bundle.add_argument("bundle_args", nargs=argparse.REMAINDER)
    bundle.set_defaults(func=cmd_bundle)

    runs = sub.add_parser("runs", help="Check analysis runs in a study folder.")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    verify = runs_sub.add_parser(
        "verify", help="Recompute run hashes and report changed, missing or added files."
    )
    verify.add_argument("workspace", help="The study folder.")
    verify.add_argument("--run", help="Only this run id.")
    verify.set_defaults(func=cmd_runs_verify)

    version = sub.add_parser("version", help="Print the version.")
    version.set_defaults(func=cmd_version)
    return parser


def _ensure_streams() -> None:
    """Under ``pythonw.exe`` (the Windows shortcut) there is no console: stdout and stderr
    are None. Point them at the null device so prints and log handlers can't fail."""
    import os

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - process lifetime
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - process lifetime


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    args = build_parser().parse_args(argv)
    return args.func(args)
