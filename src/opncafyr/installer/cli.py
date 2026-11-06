"""Installer command line, reached through ``install.sh`` / ``install.bat`` (``run_installer.py``).

    install    [--from-bundle DIR|ZIP] [--assets DIR ...] [--with-libreoffice] [--dry-run]
               [--cacert FILE] [--proxy URL]
    repair     same options as install
    uninstall  [--force] [--dry-run]
    status

Exit status: 0 done (or nothing to do), 1 failed, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from opncafyr.installer import payload as payload_mod
from opncafyr.installer.layout import InstallerBusy, Layout
from opncafyr.installer.verify import InstallError


def _say(message: str) -> None:
    print(message, flush=True)


def _source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--from-bundle",
        type=Path,
        metavar="DIR|ZIP",
        help="install from an offline bundle only; never use the network",
    )
    p.add_argument(
        "--assets",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="a folder of pinned files to use before downloading (e.g. the private opnca-r "
        "binary); may be repeated",
    )
    p.add_argument(
        "--with-libreoffice",
        action="store_true",
        help="also install LibreOffice for deckifyr slide previews",
    )
    p.add_argument("--dry-run", action="store_true", help="check and report; change nothing")
    p.add_argument("--cacert", type=Path, help="CA bundle for HTTPS (corporate proxies)")
    p.add_argument("--proxy", help="HTTPS proxy URL (default: HTTPS_PROXY)")
    p.add_argument(
        "--reinstall",
        action="store_true",
        help="install again even if this version is already installed",
    )
    p.add_argument("--skip-disk-check", action="store_true", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install", description="opncafyr per-user installer (no administrator rights)."
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    _source_args(sub.add_parser("install", help="install or update opncafyr"))
    _source_args(sub.add_parser("repair", help="check the installed opncafyr and fix it"))
    un = sub.add_parser("uninstall", help="remove opncafyr (never your workspaces)")
    un.add_argument("--force", action="store_true", help="even if opncafyr is running")
    un.add_argument("--dry-run", action="store_true", help="list what would be removed")
    un.add_argument("--leave", type=Path, help=argparse.SUPPRESS)
    sub.add_parser("status", help="show the active install")
    return parser


def status(layout: Layout) -> int:
    active = layout.read_current()
    _say(f"app root: {layout.root}")
    _say(f"active:   {active or 'none'}")
    for install_id in layout.install_ids():
        record = layout.record(install_id)
        state = "complete" if record else "incomplete"
        _say(f"  {install_id}: {state}{' (active)' if install_id == active else ''}")
    return 0


def main(argv: list[str] | None = None, *, payload_dir: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    layout = Layout.default()
    try:
        if args.mode == "status":
            return status(layout)
        if args.mode == "uninstall":
            from opncafyr.installer.uninstall import uninstall

            return uninstall(
                layout, force=args.force, dry_run=args.dry_run, say=_say, leave=args.leave
            )
        from opncafyr.installer.install import Installer, Options

        if payload_dir is None:
            raise InstallError("No installer payload: run install.sh or install.bat.")
        payload = payload_mod.load(payload_dir)
        opts = Options(
            mode=args.mode,
            from_bundle=args.from_bundle,
            assets=list(args.assets),
            with_libreoffice=args.with_libreoffice,
            dry_run=args.dry_run,
            cacert=args.cacert,
            proxy=args.proxy,
            skip_disk_check=args.skip_disk_check,
            reinstall=args.reinstall,
        )
        return Installer(layout, payload, opts, _say).run()
    except (InstallError, InstallerBusy) as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("interrupted: the previous install is still active", file=sys.stderr)
        return 130
