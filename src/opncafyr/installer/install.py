"""Install, update and repair: one code path for every source of files.

1. **Preflight:** platform and OS version, not root, the app root writable, free disk.
2. **Resolve and verify:** every pinned file is found (cache, bundle, ``--assets``) or
   downloaded, and hashed against the pins. Nothing is staged until all of them pass.
3. **Stage** into ``versions/<id>`` (``steps``), then smoke tests and the self-check.
4. **Commit:** ``install.json`` is written last; without it the folder is incomplete.
5. **Activate:** the ``current`` pointer flips atomically; icons and the payload copy follow.

Git (#56) is optional but installed by default, and best effort: if its file can't be
fetched or it can't be unpacked and started, the install goes on without it and the
install record (``optional``) and the report say why; version history is then unavailable.

Any failure before step 5 removes the incomplete folder, writes a redacted support bundle
and leaves the previous install active. A second ``install`` of the same payload is a
no-op (it only repairs a missing icon). ``repair`` checks the active install and, if a part
is damaged, installs it afresh into a new id: from the verified download cache, or from
the same ``--from-bundle``/``--assets`` source the install used (files read from a local
source are used in place, not copied into the cache).

``OPNCAFYR_INSTALL_FAIL_AFTER=<step>`` makes the run fail after that step (tests and CI;
``staging`` is the point after everything is staged and checked, just before the commit).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opncafyr.installer import icons, linux_libs, platforms, proc, smoke, support
from opncafyr.installer.layout import INSTALL_RECORD, Layout, remove_tree
from opncafyr.installer.payload import Payload
from opncafyr.installer.pins import Asset, Pins
from opncafyr.installer.records import write_json
from opncafyr.installer.sources import Cache, DownloadSource, LocalSource, resolve
from opncafyr.installer.steps import Context, steps, venv_python
from opncafyr.installer.verify import InstallError, sha256_bytes

FAIL_ENV = "OPNCAFYR_INSTALL_FAIL_AFTER"
MIN_FREE_BYTES = 4 * (1 << 30)
Say = Callable[[str], None]


@dataclass
class Options:
    mode: str = "install"  # install | repair
    from_bundle: Path | None = None
    assets: list[Path] = field(default_factory=list)
    mirror: Path | None = None
    with_libreoffice: bool = False
    with_git: bool = True  # #56: best effort; --without-git leaves it out
    dry_run: bool = False
    cacert: Path | None = None
    proxy: str | None = None
    skip_disk_check: bool = False
    reinstall: bool = False  # stage again even if the same payload is installed
    # repair only (#34): build opnca-r from its vendored source instead of the binary
    build_from_source: bool = False
    # Development only: install a platform that is still pending (no opnca-r binary yet,
    # Linux until opnca-r#61) without opnca-r. Analysis can't run; everything else can.
    allow_pending: bool = False

    @property
    def offline(self) -> bool:
        """Bundle mode: no network at all, not even as a fallback."""
        return self.from_bundle is not None

    @property
    def source(self) -> str:
        if self.from_bundle is not None:
            return "bundle"
        if self.mirror is not None:
            return "mirror"
        return "online"


def identity(payload: Payload, platform: str, opts: Options) -> str:
    """What makes two installs the same: the payload, the platform and the options.

    ``build_from_source`` isn't part of it: a source-built install is the same install, so
    a later ``install`` of the same version keeps it, and an update returns to binaries.
    """
    doc = {
        "payload": payload.digest,
        "platform": platform,
        "libreoffice": opts.with_libreoffice,
        "git": opts.with_git,
    }
    if opts.allow_pending:
        doc["allow_pending"] = True  # a different install from the complete one
    return sha256_bytes(json.dumps(doc, sort_keys=True).encode())


def run_selfcheck(ctx: Context, draft: Path) -> None:
    """Validate the records with the new launcher environment (``selfcheck.py``)."""
    proc.run(
        [venv_python(ctx.dir / "launcher"), "-I", "-m", "opncafyr.installer.selfcheck"]
        + [ctx.dir, draft],
        env=ctx.env(),
        log=ctx.log("selfcheck"),
    )


def maybe_fail(step: str) -> None:
    if os.environ.get(FAIL_ENV) == step:
        raise InstallError(f"simulated failure after {step} ({FAIL_ENV})")


# --- checks of an existing install -----------------------------------------------------


def runtime_doc(layout: Layout, install_id: str) -> dict[str, Any]:
    path = layout.version_dir(install_id) / "runtime" / "runtime.json"
    return json.loads(path.read_text("utf-8"))


def quick_check(layout: Layout, install_id: str) -> list[str]:
    """Problems with an install's files (cheap: no process is started)."""
    problems = []
    if layout.record(install_id) is None:
        return [f"{install_id}: {INSTALL_RECORD} is missing or incomplete"]
    try:
        doc = runtime_doc(layout, install_id)
    except (OSError, ValueError) as exc:
        return [f"runtime.json: {exc}"]
    for name, path in doc.get("executables", {}).items():
        if not Path(path).is_file():
            problems.append(f"{name}: {path} is missing")
    for key in ("r_library", "wheelhouse"):
        if key in doc and not Path(doc[key]).is_dir():
            problems.append(f"{key}: {doc[key]} is missing")
    return problems


def expected_versions(
    pins: Pins, payload: Payload, *, without_opnca: bool = False
) -> dict[str, str]:
    versions = {
        "opncafyr": payload.version,
        "deckifyr": pins.version("deckifyr"),
        "opnca-r": pins.version("opnca-r"),
        "quarto": pins.version("quarto"),
    }
    if without_opnca:
        del versions["opnca-r"]  # the smoke checks then leave out what needs opnca-r
    return versions


# --- the installer ---------------------------------------------------------------------


class Installer:
    def __init__(self, layout: Layout, payload: Payload, opts: Options, say: Say) -> None:
        self.layout = layout
        self.payload = payload
        self.opts = opts
        self._say = say
        self.run_log: support.RunLog | None = None
        self.bundle: dict[str, Any] | None = None
        if opts.from_bundle is not None:
            from opncafyr.installer.bundle import read_manifest

            # For the record and the report only: hashes always come from the pins.
            manifest = read_manifest(opts.from_bundle)
            if manifest and isinstance(manifest.get("bundle_id"), str):
                self.bundle = manifest

    def say(self, message: str) -> None:
        self._say(message)
        if self.run_log is not None:
            self.run_log.write(message)

    # --- preflight -------------------------------------------------------------------
    def preflight(self) -> tuple[str, str]:
        platform = platforms.detect()
        os_desc = platforms.check_os_version(platform)
        if platform.startswith("linux"):
            # No apt and no root: the system libraries must already be there (#65).
            linux_libs.check_libraries()
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise InstallError("Don't run the installer as root: it installs for one user.")
        root = self.layout.root
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".write-probe-{os.getpid()}"
        try:
            probe.write_text("ok", "utf-8")
            probe.unlink()
        except OSError as exc:
            raise InstallError(f"Can't write to {root}: {exc}") from None
        if not self.opts.skip_disk_check:
            free = shutil.disk_usage(root).free
            if free < MIN_FREE_BYTES:
                raise InstallError(
                    f"Not enough free disk space: {free >> 20} MB free, "
                    f"{MIN_FREE_BYTES >> 20} MB needed under {root}."
                )
        if self.opts.build_from_source and sys.platform == "darwin":
            from opncafyr.installer.source_build import check_command_line_tools

            check_command_line_tools()
        return platform, os_desc

    # --- sources ---------------------------------------------------------------------
    def _sources(self) -> tuple[Cache, list[LocalSource], DownloadSource | None]:
        cache = Cache(self.layout.downloads)
        local = []
        for location in [
            *([self.opts.from_bundle] if self.opts.from_bundle else []),
            *self.opts.assets,
        ]:
            try:
                local.append(LocalSource(location, cache))
            except FileNotFoundError as exc:
                raise InstallError(str(exc)) from None
        download = None
        if not self.opts.offline:
            rewrite = None
            if self.opts.mirror is not None:
                from opncafyr.installer.mirror import Mirror

                rewrite = Mirror.load(self.opts.mirror).rewrite
            log = self.run_log.dir / "downloads.log" if self.run_log else Path(os.devnull)
            download = DownloadSource(
                cache,
                log=log,
                cacert=self.opts.cacert,
                proxy=self.opts.proxy,
                rewrite=rewrite,
                user_agent=f"opncafyr-installer/{self.payload.version}",
            )
        return cache, local, download

    def _resolve(self, assets: list[Asset], skipped: dict[str, str]) -> dict[str, Path]:
        cache, local, download = self._sources()
        try:
            return resolve(
                assets,
                cache=cache,
                local=local,
                download=download,
                progress=self.say,
                skipped=skipped,
            )
        finally:
            for source in local:
                source.close()

    # --- dry run ---------------------------------------------------------------------
    def dry_run(self, platform: str, assets: list[Asset]) -> int:
        cache, local, _ = self._sources()
        cached = sum(1 for a in assets if cache.path(a).is_file())
        self.say(f"platform {platform}, app root {self.layout.root}")
        self.say(f"active install: {self.layout.read_current() or 'none'}")
        self.say(f"{len(assets)} pinned files: {cached} already in the download cache")
        if local:
            self.say("local sources: " + ", ".join(s.label for s in local))
        if self.opts.offline:
            self.say("bundle mode: no network access")
        self.say("dry run: nothing was changed")
        return 0

    # --- main ------------------------------------------------------------------------
    def run(self) -> int:
        platform, os_desc = self.preflight()
        pins = Pins.load(self.payload.runtime)
        assets = pins.assets(
            platform,
            libreoffice=self.opts.with_libreoffice,
            git=self.opts.with_git,
            source_build=self.opts.build_from_source,
            allow_pending=self.opts.allow_pending,
        )
        if self.opts.dry_run:
            return self.dry_run(platform, assets)
        with self.layout.install_lock():
            self.run_log = support.RunLog(self.layout.logs, self.opts.mode)
            self.say(f"opncafyr {self.payload.version} {self.opts.mode} on {platform} ({os_desc})")
            for stale in self.layout.incomplete_ids():
                self.say(f"removing incomplete install {stale}")
                self.layout.remove_install(stale)
            active = self.layout.read_current()
            ident = identity(self.payload, platform, self.opts)
            if self.opts.mode == "repair":
                if active is None:
                    raise InstallError("Nothing is installed to repair. Run `install` instead.")
                if self.opts.build_from_source:
                    # Always a new install: the active one stays until the build passes.
                    self.say(
                        f"building opnca-r from source into a new install; {active} stays active"
                    )
                else:
                    problems = self.check_active(active, pins)
                    if not problems:
                        self.refresh(active)
                        self.say(f"{active} checked: nothing to repair")
                        return 0
                    self.say(f"{active} is damaged: " + "; ".join(problems))
            elif active is not None and not self.opts.reinstall:
                record = self.layout.record(active) or {}
                if record.get("identity") == ident and not quick_check(self.layout, active):
                    self.refresh(active)
                    self.say(f"opncafyr {self.payload.version} is already installed ({active})")
                    return 0
            return self.stage_and_activate(platform, os_desc, pins, assets, ident, active)

    def _without_opnca(self, pins: Pins) -> bool:
        return self.opts.allow_pending and platforms.detect() in pins.pending

    def check_active(self, active: str, pins: Pins) -> list[str]:
        problems = quick_check(self.layout, active)
        if problems:
            return problems
        vdir = self.layout.version_dir(active)
        tmpdir = proc.space_free_tmp(f"check-{active}", vdir / "tmp" / "t")
        env = proc.base_env(offline=True, cache=self.layout.cache, tmpdir=tmpdir)
        results = smoke.run_all(
            runtime_doc(self.layout, active),
            versions=expected_versions(pins, self.payload, without_opnca=self._without_opnca(pins)),
            env=env,
            work=vdir / "tmp" / "smoke",
            log=self.run_log.dir / "smoke.log" if self.run_log else None,
        )
        shutil.rmtree(vdir / "tmp", ignore_errors=True)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return [f"{r['name']}: {r['detail']}" for r in results if r["status"] != "ok"]

    def refresh(self, active: str) -> None:
        """Recreate a missing icon and the payload copy of the active install."""
        log = self.run_log.dir / "icons.log" if self.run_log else Path(os.devnull)
        if not icons.present(self.layout):
            try:
                icons.create(self.layout, active, self.payload.version, log)
                self.say("recreated the opncafyr icon")
            except (proc.ProcError, OSError) as exc:
                self.say(f"warning: could not create the icon: {exc}")
        self._refresh_installer_copy(active)

    def _refresh_installer_copy(self, active: str) -> None:
        src = self.layout.version_dir(active) / "installer"
        dest = self.layout.installer_copy
        if not src.is_dir():
            return
        try:
            if dest.resolve() == Path(self.payload.directory).resolve():
                return  # running from it; it is the same payload
            tmp = dest.with_name(dest.name + ".tmp")
            remove_tree(tmp)
            shutil.copytree(src, tmp)
            remove_tree(dest)
            tmp.rename(dest)
        except OSError as exc:
            self.say(f"warning: could not refresh {dest}: {exc}")

    def stage_and_activate(
        self,
        platform: str,
        os_desc: str,
        pins: Pins,
        assets: list[Asset],
        ident: str,
        previous: str | None,
    ) -> int:
        if self.run_log is None:
            raise RuntimeError("no run log")
        self.say(f"checking {len(assets)} pinned files ({self.opts.source})")
        started = time.monotonic()
        skipped: dict[str, str] = {}
        files = self._resolve(assets, skipped)
        self.run_log.step("verify", "ok", time.monotonic() - started, f"{len(files)} files")
        self.say(f"all {len(files)} files match their pinned SHA-256")
        for component, problem in sorted(skipped.items()):
            self.say(
                f"warning: optional {component} could not be fetched; installing without it: "
                f"{problem}"
            )
        assets = [a for a in assets if not (a.best_effort and a.component in skipped)]
        maybe_fail("verify")

        install_id = self.layout.next_install_id(self.payload.version)
        ctx = Context(
            layout=self.layout,
            platform=platform,
            install_id=install_id,
            pins=pins,
            payload=self.payload,
            assets=assets,
            files=files,
            log_dir=self.run_log.dir,
            offline=self.opts.offline,
            say=self.say,
            with_libreoffice=self.opts.with_libreoffice,
            with_git=self.opts.with_git,
            build_from_source=self.opts.build_from_source,
            without_opnca=self._without_opnca(pins),
        )
        for component, problem in skipped.items():
            ctx.report_optional(component, "failed", f"not fetched: {problem}"[-500:])
        step = "stage"
        try:
            ctx.dir.mkdir(parents=True)
            ctx.tmp.mkdir()
            for step, fn in steps():
                t0 = time.monotonic()
                self.say(f"[{step}]")
                fn(ctx)
                self.run_log.step(step, "ok", time.monotonic() - t0)
                maybe_fail(step)
            step = "smoke"
            t0 = time.monotonic()
            self.say("[smoke tests]")
            results = smoke.run_all(
                runtime_doc(self.layout, install_id),
                versions=expected_versions(
                    pins, self.payload, without_opnca=self._without_opnca(pins)
                ),
                env=ctx.env(),
                work=ctx.tmp / "smoke",
                log=ctx.log("smoke"),
            )
            for r in results:
                self.say(f"  {r['status']:<6} {r['name']}: {r['detail']}")
            if any(r["status"] != "ok" for r in results):
                raise smoke.SmokeFailed(results)
            self.run_log.step("smoke", "ok", time.monotonic() - t0)
            maybe_fail("smoke")
            step = "selfcheck"
            record = self.install_record(ctx, os_desc, ident, results)
            draft = ctx.tmp / INSTALL_RECORD
            write_json(draft, record)
            run_selfcheck(ctx, draft)
            step = "staging"
            maybe_fail("staging")
            step = "commit"
            remove_tree(ctx.tmp)
            if sys.platform == "darwin":
                remove_tree(ctx.tmpdir)
            write_json(ctx.dir / INSTALL_RECORD, record)
        except BaseException as exc:
            self.run_log.step(step, "failed", 0, str(exc)[-500:])
            self.run_log.write(f"FAILED at {step}: {exc!r}\n{getattr(exc, 'tail', '')}")
            bundle = support.write_bundle(
                self.run_log,
                self.layout.logs / "support",
                failed_step=step,
                error=str(exc),
                version=self.payload.version,
            )
            try:
                self.layout.remove_install(install_id)
                if sys.platform == "darwin":
                    remove_tree(ctx.tmpdir)
            except OSError as cleanup:
                self.say(f"warning: could not remove the incomplete {install_id}: {cleanup}")
            if not isinstance(exc, Exception):
                raise
            still = previous or "none"
            raise InstallError(
                f"Install failed at step '{step}': {exc}\n"
                f"Previous install still active: {still}\n"
                f"Support bundle (redacted): {bundle}\nLogs: {self.run_log.dir}"
            ) from None
        self.activate(install_id, previous)
        self.write_report(install_id)
        self.say(
            f"opncafyr {self.payload.version} installed ({install_id}) in "
            f"{time.monotonic() - started:.0f} s"
        )
        for name, outcome in sorted(ctx.optional.items()):
            if outcome["status"] == "failed":
                self.say(
                    f"note: the optional {name} is not installed ({outcome['detail']}). "
                    "Everything else works; `install --reinstall` tries again."
                )
        return 0

    def install_record(
        self, ctx: Context, os_desc: str, ident: str, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "complete",
            "install_id": ctx.install_id,
            "version": self.payload.version,
            "platform": ctx.platform,
            "os": os_desc,
            "identity": ident,
            "payload_digest": self.payload.digest,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mode": self.opts.mode,
            "source": self.opts.source,
            "options": {
                "libreoffice": self.opts.with_libreoffice,
                "build_from_source": self.opts.build_from_source,
            },
            "paths": {
                "app_root": str(self.layout.root),
                "version_dir": str(ctx.dir),
                "r_dir": str(ctx.r_dir),
            },
            "components": [{"name": k, **v} for k, v in sorted(ctx.components.items())],
            "optional": [{"name": k, **v} for k, v in sorted(ctx.optional.items())],
            "smoke": results,
            **({"bundle_id": self.bundle["bundle_id"]} if self.bundle else {}),
        }

    def write_report(self, install_id: str) -> None:
        """The installation verification report (#36): JSON and HTML in state/reports."""
        from opncafyr.installer import report

        record = self.layout.record(install_id)
        if record is None:
            return
        try:
            json_path, html_path = report.write(
                self.layout, report.build(self.layout, record, bundle=self.bundle)
            )
        except OSError as exc:
            self.say(f"warning: could not write the installation report: {exc}")
            return
        self.say(f"installation report: {html_path}")
        self.say(f"                     {json_path}")

    def activate(self, install_id: str, previous: str | None) -> None:
        self.layout.write_current(install_id)
        self.say(f"activated {install_id}")
        log = self.run_log.dir / "icons.log" if self.run_log else Path(os.devnull)
        try:
            icons.create(self.layout, install_id, self.payload.version, log)
        except (proc.ProcError, OSError) as exc:
            self.say(f"warning: could not create the icon (run `repair` later): {exc}")
        if sys.platform.startswith("linux"):
            launcher = icons.linux_launcher(self.layout)
            self.say(
                f"start it with: {launcher} launch --remote --port 8765 --workspace <study folder>"
            )
            self.say(
                "  (no desktop here: reach it through an SSH forward or your IDE's proxy; "
                "see the guide)"
            )
        self._refresh_installer_copy(install_id)
        self.collect_garbage(keep={install_id, *([previous] if previous else [])})

    def collect_garbage(self, keep: set[str]) -> None:
        """Keep the active and the previous install; remove older ones."""
        if self.layout.launcher_running():
            return  # a running launcher may still use an older install
        for old in self.layout.complete_ids():
            if old in keep:
                continue
            try:
                self.layout.remove_install(old)
                self.say(f"removed old install {old}")
            except OSError as exc:
                self.say(f"warning: could not remove {old}: {exc}")
