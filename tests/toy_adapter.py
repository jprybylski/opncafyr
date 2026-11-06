"""A WebAppAdapter for ``tests/toy_child.py``, plus helpers to build a supervisor."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from opncafyr.core.adapter import ChildView, LaunchContext, LaunchSpec
from opncafyr.core.runtime import Runtime
from opncafyr.core.supervisor import Supervisor, WebAppConfig

TOY_SCRIPT = Path(__file__).with_name("toy_child.py")
TOKEN_HEADER = "X-Toy-Token"


class ToyAdapter:
    def __init__(self) -> None:
        self.stops: list[int] = []

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        argv = [
            sys.executable,
            str(TOY_SCRIPT),
            "--port",
            str(ctx.port),
            "--base-path",
            ctx.base_path,
            "--token-env",
            "TOY_TOKEN",
        ]
        if session.get("grandchild"):
            argv.append("--grandchild")
        if session.get("fail_first"):
            argv += ["--fail-first", str(session["fail_first"])]
        if session.get("never_ready"):
            argv.append("--never-ready")
        for line in session.get("say", ()):
            argv.append(f"--say={line}")
        env = {"TOY_TOKEN": ctx.token, **session.get("env", {})}
        return LaunchSpec(
            argv=argv,
            env=env,
            auth_headers={TOKEN_HEADER: ctx.token},
            ready_line_prefix="TOY_READY ",
        )

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool:
        return client.get(child.context.base_url + "api/health").status_code == 200

    def stop(self, child: ChildView, client: httpx.Client) -> None:
        self.stops.append(child.pid)
        client.post(child.context.base_url + "api/shutdown", headers=child.spec.auth_headers)


def ready_info(child: ChildView) -> dict[str, Any]:
    assert child.ready_line is not None
    return json.loads(child.ready_line.split(" ", 1)[1])


def toy_supervisor(tmp_path: Path, **kwargs: Any) -> tuple[Supervisor, ToyAdapter]:
    adapter = ToyAdapter()
    apps = {"toy": WebAppConfig(adapter=adapter, route="/apps/toy/", max_body_bytes=8 << 20)}
    kwargs.setdefault("ready_timeout", 20.0)
    kwargs.setdefault("stop_grace", 5.0)
    sup = Supervisor(
        apps,
        runtime=Runtime(),
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
        **kwargs,
    )
    return sup, adapter
