"""opnca adapter (#27, #28).

Implements the web-app part of ``opncafyr.core.adapter``: start, ready and stop for
opnca-r's ``scripts/serve.R`` (pinned in ``runtime/pins.toml``). Executables come from the
installed runtime, never from workspace content. Analysis logic stays in opnca-r.

The child's process tree is killed after the orderly stop (``core.supervisor``): every
descendant on POSIX, the whole Job Object on Windows, where the pid in ``OPNCA_READY``
is a child of ``Rscript.exe``. The supervisor gives the child its own TMP folder and
deletes it after the stop, so R temp files holding data don't outlive the session.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from opncafyr.core.adapter import ChildView, LaunchContext, LaunchSpec

API_TOKEN_HEADER = "X-Opnca-Api-Token"  # noqa: S105 - a name, not a secret
SHUTDOWN_TOKEN_HEADER = "X-Opnca-Shutdown-Token"  # noqa: S105 - a name, not a secret
API_TOKEN_ENV = "OPNCA_API_TOKEN"  # noqa: S105 - a name, not a secret
SHUTDOWN_TOKEN_ENV = "OPNCA_SHUTDOWN_TOKEN"  # noqa: S105 - a name, not a secret
READY_PREFIX = "OPNCA_READY "
# Also limits export/save, which carries up to 8 base64 figures of 2 MiB each. Must match
# web_app.max_body_bytes in descriptor.yaml (tested).
MAX_UPLOAD_BYTES = 25_165_824
_NAMESPACE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _absolute(session: Mapping[str, Any], key: str, *, directory: bool) -> Path:
    value = session.get(key)
    if not value:
        raise ValueError(f"opnca session needs {key!r}.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"opnca session {key!r} must be an absolute path.")
    if directory and not path.is_dir():
        raise ValueError(f"opnca session {key!r} is not a directory.")
    if not directory and not path.is_file():
        raise ValueError(f"opnca session {key!r} is not a file.")
    return path


class OpncaWebApp:
    """One opnca-r session per run, served under ``/apps/opnca/``.

    ``session`` keys (all resolved by the caller with ``resolve_in_root``):
    ``data_path`` (the frozen one-analyte ``input/analysis.csv``), ``output_dir`` (the
    run directory), ``run_id``, and optionally ``mapping_path``, ``label`` and
    ``context`` (a mapping of display key/values).
    """

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        rscript = ctx.runtime.executable("rscript")
        library = ctx.runtime.require_r_library()
        serve = library / "opnca" / "scripts" / "serve.R"
        if not serve.is_file():
            raise FileNotFoundError(f"opnca-r serve.R not found in the runtime: {serve}")
        data = _absolute(session, "data_path", directory=False)
        output = _absolute(session, "output_dir", directory=True)
        run_id = str(session.get("run_id", ""))
        if not _NAMESPACE.match(run_id):
            raise ValueError("opnca session run_id must match ^[A-Za-z0-9._-]{1,64}$.")

        # "--flag=value" so a value can never be read as another flag.
        argv = [
            str(rscript),
            "--vanilla",
            str(serve),
            f"--data-path={data}",
            f"--output-dir={output}",
            f"--port={ctx.port}",
            f"--base-path={ctx.base_path}",
            "--lock-dataset",
            "--no-persist-mapping",
            f"--storage-namespace={run_id}",
            "--no-overwrite",
            "--require-blq-rules",
            # 0.2.8: no default BLQ method either, and no role guessed from column names,
            # so the session uses exactly the preset the user confirmed (ADR 0005 Q3).
            "--require-explicit-settings",
            "--no-guess-roles",
            f"--api-token-env={API_TOKEN_ENV}",
            f"--shutdown-token-env={SHUTDOWN_TOKEN_ENV}",
            f"--max-upload-bytes={MAX_UPLOAD_BYTES}",
        ]
        if session.get("mapping_path"):
            argv.append(f"--mapping={_absolute(session, 'mapping_path', directory=False)}")
        if session.get("label"):
            argv.append(f"--label={session['label']}")
        for key, value in dict(session.get("context") or {}).items():
            argv.append(f"--context={key}={value}")

        env = {
            "R_LIBS": str(library),
            "R_LIBS_USER": str(library),
            API_TOKEN_ENV: ctx.token,
            SHUTDOWN_TOKEN_ENV: secrets.token_urlsafe(32),
        }
        return LaunchSpec(
            argv=argv,
            env=env,
            path_dirs=[rscript.parent],
            auth_headers={API_TOKEN_HEADER: ctx.token},
            ready_line_prefix=READY_PREFIX,
        )

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool:
        if child.ready_line is None:
            return False
        info = json.loads(child.ready_line[len(READY_PREFIX) :])
        if info.get("url") != child.context.base_url:
            return False
        return client.get(child.context.base_url + "api/v1/health").status_code == 200

    def stop(self, child: ChildView, client: httpx.Client) -> None:
        headers = {
            **child.spec.auth_headers,
            SHUTDOWN_TOKEN_HEADER: child.spec.env[SHUTDOWN_TOKEN_ENV],
        }
        client.post(child.context.base_url + "api/v1/shutdown", headers=headers)


def web_app() -> OpncaWebApp:
    return OpncaWebApp()
