"""Optional LibreOffice for deckifyr slide previews (#8, docs/spikes/libreoffice.md).

Installed only with ``--with-libreoffice``. macOS: ``ditto`` the app out of the DMG.
Windows: an administrative install (``msiexec /a``) into the version folder, which needs no
UAC in a desktop session; elsewhere ``expand_msi.ps1`` unpacks the MSI instead. The
MSI's own VC++ runtime DLLs are never copied next to ``soffice.exe``.
"""

from __future__ import annotations

import os
import sys
from importlib import resources

from opncafyr.installer import proc
from opncafyr.installer.archives import find_one
from opncafyr.installer.steps import Context


def install(ctx: Context) -> None:
    if not ctx.with_libreoffice:
        return
    asset = ctx.asset("libreoffice")
    dest = ctx.dir / "libreoffice"
    log = ctx.log("libreoffice")
    env = ctx.env()
    if sys.platform == "darwin":
        mount = ctx.tmpdir / "lo-mount"
        mount.mkdir(parents=True, exist_ok=True)
        proc.run(
            ["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount]
            + [ctx.file("libreoffice")],
            env=env,
            log=log,
        )
        try:
            dest.mkdir(parents=True, exist_ok=True)
            proc.run(
                ["/usr/bin/ditto", mount / "LibreOffice.app", dest / "LibreOffice.app"],
                env=env,
                log=log,
            )
        finally:
            proc.run(["/usr/bin/hdiutil", "detach", mount], env=env, log=log, check=False)
        soffice = dest / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
    else:
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        msiexec = os.path.join(root, "System32", "msiexec.exe")
        done = proc.run(
            [msiexec, "/a", ctx.file("libreoffice"), "/qn", f"TARGETDIR={dest}"]
            + ["/L*v", ctx.log_dir / "libreoffice-msi.log"],
            env=env,
            log=log,
            check=False,
            timeout=1800,
        )
        if done.returncode != 0:
            # 1601 in a non-interactive logon: unpack the MSI without Windows Installer.
            script = ctx.tmp / "expand_msi.ps1"
            script.write_text(
                resources.files("opncafyr.installer").joinpath("ps", "expand_msi.ps1").read_text(),
                "utf-8",
            )
            ps = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            proc.run(
                [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
                + [script, "-Msi", ctx.file("libreoffice"), "-Dest", dest],
                env=env,
                log=log,
                timeout=3600,
            )
        soffice = find_one(dest, "program/soffice.exe")
    if not soffice.is_file():
        raise FileNotFoundError(f"no soffice at {soffice}")
    ctx.executables["soffice"] = soffice
    ctx.record("libreoffice", asset.version, [asset])
