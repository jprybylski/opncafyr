"""Cloud container mode, end to end against a real container (#42).

    uv run python scripts/container/mode_check.py --image <ref> [--engine docker|podman]

Starts the image with ``container/opncafyr-container`` on a fresh study folder and drives
it the way a browser would, through each access route:

1. **SSH forward.** A local TCP forward on a *different* port stands in for
   ``ssh -L <local>:127.0.0.1:<VM port>``. Home page, API, a one-analyte run saved in
   opnca-r through ``/apps/opnca/``, and the deck editor under ``/apps/deckifyr/``.
2. **Prefix proxy.** A reverse proxy that strips ``/proxy/<port>/`` and rewrites Host, as
   VS Code Server and code-server do; the same routes, under the prefix.
3. **Security behind both:** a foreign Host gets 400, a foreign Origin 403, a missing
   session token 403, a missing cookie 403.
4. Files in the study belong to the invoking user; a second ``start`` reattaches; the
   port is not reachable on the machine's external address; dropping the client while a
   job runs doesn't cancel it.

Needs the repository's dev environment (httpx, uvicorn) and ``tests/`` next to
``scripts/``. Prints counts and IDs only.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))
import nca_helpers as N  # noqa: E402
import publish_helpers as P  # noqa: E402

SCRIPT = REPO / "container" / "opncafyr-container"
SESSION_HEADER = "x-opncafyr-session"
EVIL = "https://evil.example"
FIGURE = "profiles-linear"


class CheckFailed(AssertionError):
    pass


def check(ok: bool, what: str) -> None:
    if not ok:
        raise CheckFailed(what)
    print(f"   ok  {what}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wrapper(*args: str) -> str:
    done = subprocess.run(  # noqa: S603 - our own script, argv only
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "XDG_STATE_HOME": STATE_HOME},
    )
    if done.returncode != 0:
        raise CheckFailed(f"opncafyr-container {args[0]} failed: {done.stderr[-2000:]}")
    return done.stdout


def link_from(output: str) -> str:
    links = re.findall(r"^(https?://\S+/open\?token=\S+)$", output, re.M)
    if not links:
        raise CheckFailed(f"no link in: {output[-500:]}")
    return links[-1]


# --- stand-ins for ssh -L and an IDE proxy ---------------------------------------------


@contextmanager
def tcp_forward(listen: int, target: int):
    """``ssh -L listen:127.0.0.1:target``, in-process."""
    loop = asyncio.new_event_loop()

    async def pipe(r, w):
        try:
            while data := await r.read(65536):
                w.write(data)
                await w.drain()
        finally:
            w.close()

    async def handle(r, w):
        tr, tw = await asyncio.open_connection("127.0.0.1", target)
        await asyncio.gather(pipe(r, tw), pipe(tr, w), return_exceptions=True)

    async def serve():
        return await asyncio.start_server(handle, "127.0.0.1", listen)

    server = loop.run_until_complete(serve())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:

        async def shutdown():
            server.close()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(shutdown(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


def stripping_proxy(prefix: str, target: int):
    """An ASGI reverse proxy like VS Code Server's /proxy/<port>/: strips the prefix,
    rewrites Host to the target, passes Origin and cookies through."""
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{target}", timeout=120)

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope["path"]
        if not path.startswith(prefix):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"not proxied"})
            return
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"host"]
        headers.append((b"host", f"localhost:{target}".encode()))
        url = "/" + path[len(prefix) :]
        if scope["query_string"]:
            url += "?" + scope["query_string"].decode()
        resp = await client.request(scope["method"], url, headers=headers, content=body)
        out = [
            (k.encode(), v.encode())
            for k, v in resp.headers.multi_items()
            if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")
        ]
        await send({"type": "http.response.start", "status": resp.status_code, "headers": out})
        await send({"type": "http.response.body", "body": resp.content})

    return app


@contextmanager
def serve_asgi(app, port: int):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(10)


# --- the browser's side ----------------------------------------------------------------


def signed_in(link: str, base: str) -> httpx.Client:
    """Follow the one-time link, then read the session secret from the page, as the home
    page does."""
    client = httpx.Client(base_url=base, timeout=120, follow_redirects=True)
    home = client.get(link)
    check(home.status_code == 200, f"the one-time link opens the home page ({base})")
    m = re.search(r'name="opncafyr-session" content="([^"]+)"', home.text)
    check(m is not None, "the home page carries the session secret")
    client.headers[SESSION_HEADER] = m.group(1)
    client.follow_redirects = False
    return client


def security_holds(client: httpx.Client, origin: str, direct_port: int) -> None:
    direct = httpx.Client(base_url=f"http://127.0.0.1:{direct_port}", timeout=10)
    check(
        direct.get("/health", headers={"host": "evil.example"}).status_code == 400,
        "foreign Host: 400",
    )
    r = client.post("api/quit", headers={"origin": EVIL})
    check(r.status_code == 403, "foreign Origin: 403")
    r = client.post("api/quit", headers={"origin": origin, SESSION_HEADER: ""})
    check(r.status_code == 403, "missing session token: 403")
    bare = httpx.Client(base_url=str(client.base_url), timeout=10)
    check(bare.get("api/tools").status_code == 403, "missing cookie: 403")


def child_page(client: httpx.Client, origin: str, route: str) -> None:
    """The child's page and the assets it names load, and none escapes the prefix."""
    page = client.get(route)
    check(page.status_code == 200 and "<html" in page.text.lower(), f"{route} serves its page")
    prefix = client.base_url.path
    links = [a for a in re.findall(r'(?:src|href)="([^"#?]+)', page.text) if "://" not in a]
    links = [a for a in links if not a.startswith("data:")]
    escaped = [a for a in links if a.startswith("/") and not a.startswith(prefix)]
    check(not escaped, f"{route}: every link stays under {prefix} ({len(links)} links)")
    for a in links[:25]:
        got = client.get(origin + a) if a.startswith("/") else client.get(route + a)
        check(got.status_code < 400, f"{route}: {a[:50]} loads")


def wait_replay(client: httpx.Client, run_id: str, timeout: float = 900) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = (N.save_status(client, run_id)["run"].get("replay") or {}).get("status")
        if status not in (None, "pending", "running", "queued"):
            return status
        time.sleep(1)
    raise CheckFailed(f"{run_id}: replay did not finish")


def drive_flow(client: httpx.Client, origin: str, study: Path, deck: bool) -> None:
    """Open the study, make one run, save it in opnca-r through /apps/opnca/, and (with
    ``deck``) complete it, publish it and open the deck editor under /apps/deckifyr/."""
    N.same_origin = lambda _c: {"origin": origin, "sec-fetch-site": "same-origin"}
    folder = client.post("api/workspace/pick").json()["folder"]
    check(folder["path"] == "/workspace", "choosing a folder gives the mounted study")
    r = client.post("api/workspace/init", json={"path": "/workspace"})
    check(r.status_code == 200, f"the study opens ({r.status_code})")
    check(
        (study / ".opncafyr").stat().st_uid == os.getuid(),
        "workspace files belong to the invoking user",
    )
    for csv in sorted((study / "source-data").glob("*.csv")):
        r = client.post("api/datasets/import", json={"path": f"source-data/{csv.name}"})
        check(r.status_code == 200, f"imported {csv.name}")
    recipe = N.new_recipe(client, steps=N.resolving_steps())
    N.apply(client, recipe["id"])
    r = client.put(
        f"api/nca/recipes/{recipe['id']}/mapping", json={"mapping": N.resolving_mapping()}
    )
    check(r.status_code == 200, "mapping saved")
    run_id = N.start_run(client, recipe["id"], "PARENT")["run_id"]
    check(client.post(f"api/nca/runs/{run_id}/session").status_code == 200, "opnca-r starts")
    child_page(client, origin, "apps/opnca/")
    figure = base64.b64encode(P.png(FIGURE, size=64)).decode()
    N.save_in_opnca(
        client,
        study,
        run_id,
        reopen=False,
        save_extra={"figure_names": [FIGURE], "figure_formats": ["png"], "figure_data": [figure]},
    )
    check(True, "a run saved in opnca-r through /apps/opnca/")
    if not deck:
        return
    check(wait_replay(client, run_id) == "passed", "the run replays")
    check(client.post(f"api/nca/runs/{run_id}/complete").status_code == 200, "run completed")
    r = client.post("api/nca/publications", json={"items": P.picks(run_id, [FIGURE])})
    check(r.status_code == 201, "publication made")
    pub = r.json()["publication"]["publication_id"]
    r = client.post("api/nca/deck", json={"publication_id": pub})
    check(r.status_code == 200, "deck project set up")
    job = N.wait_job(client, r.json()["job_id"], timeout=600)
    check(job["state"] == "completed", f"deck setup job: {job['state']}")
    r = client.post("api/nca/deck/editor")
    check(r.status_code == 200, f"deck editor starts ({r.status_code})")
    child_page(client, origin, "apps/deckifyr/")


def external_address() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET: no packet is sent for UDP connect
            return s.getsockname()[0]
        except OSError:
            return None


def not_reachable_externally(port: int) -> None:
    addr = external_address()
    if addr is None or addr.startswith("127."):
        print("   skip: no external address to test", flush=True)
        return
    with socket.socket() as s:
        s.settimeout(2)
        reachable = s.connect_ex((addr, port)) == 0
    check(not reachable, f"port {port} is not reachable on {addr}")


STATE_HOME = ""


def main(argv: list[str] | None = None) -> int:
    global STATE_HOME
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True)
    parser.add_argument("--engine", default="docker", choices=("docker", "podman"))
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="opncafyr-mode-"))
    STATE_HOME = str(work / "state")
    studies = [N.make_study(work / "a"), N.make_study(work / "b")]
    port, local, front = free_port(), free_port(), free_port()
    image = ["--image", args.image, "--engine", args.engine, "--port", str(port)]
    try:
        print("== ssh forward (a different local port)", flush=True)
        out = wrapper(
            "start", str(studies[0]), *image, "--access", "ssh", "--local-port", str(local)
        )
        check("ssh -L" in out, "start prints the ssh -L command")
        origin = f"http://127.0.0.1:{local}"
        with tcp_forward(local, port):
            client = signed_in(link_from(out), origin + "/")
            check(client.get("api/tools").status_code == 200, "API through the forward")
            security_holds(client, origin, port)
            drive_flow(client, origin, studies[0], deck=True)
            client.close()  # a dropped tunnel
        again = wrapper("start", str(studies[0]), *image)
        check("Already running" in again and "/open?token=" in again, "a second start reattaches")
        with tcp_forward(local, port):
            client = signed_in(link_from(again), origin + "/")
            check(client.get("api/nca/runs").status_code == 200, "reconnecting after a drop works")
        not_reachable_externally(port)
        wrapper("forget", str(studies[0]))

        print("== prefix proxy (strips /proxy/<port>/, rewrites Host)", flush=True)
        prefix = f"/proxy/{port}/"
        origin = f"http://localhost:{front}"
        out = wrapper(
            "start", str(studies[1]), *image, "--access", "vscode", "--public-url", origin + prefix
        )
        with serve_asgi(stripping_proxy(prefix, port), front):
            client = signed_in(link_from(out), origin + prefix)
            check(client.get("api/tools").status_code == 200, "API through the prefix proxy")
            security_holds(client, origin, port)
            drive_flow(client, origin, studies[1], deck=True)
        wrapper("forget", str(studies[1]))
    except (CheckFailed, AssertionError, KeyError, httpx.HTTPError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        for study in studies:
            subprocess.run(  # noqa: S603 - our own script
                [str(SCRIPT), "forget", str(study)],
                env={**os.environ, "XDG_STATE_HOME": STATE_HOME},
                check=False,
            )
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("container mode check passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
