"""Cloud container mode (#42): remote access settings, the prefix, storage, fixed folder."""

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opncafyr.cli import main
from opncafyr.core import storage
from opncafyr.core.app import create_app
from opncafyr.core.bootstrap import mint_open_token
from opncafyr.core.remote import (
    CONTAINER_BIND,
    LOOPBACK,
    RemoteAccessError,
    container_access,
    native_access,
)
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.supervisor import Supervisor

PORT = 8765

PRESETS = {
    "ssh": dict(access="ssh", local_port=9000),
    "vscode": dict(access="vscode", public_url="https://ide.example.com/proxy/8765/"),
    "jupyter": dict(access="jupyter", public_url="https://hub.example.com/user/ana/proxy/8765/"),
    "workbench": dict(access="workbench", public_url="https://wb.example.com/s/a1b2/p/c3d4/"),
}


def security_for(remote) -> SecurityConfig:
    return SecurityConfig(
        port=PORT,
        extra_hosts=remote.hosts,
        extra_origins=remote.origins,
        base_path=remote.base_path,
        cookie_id=remote.cookie_id,
        secure_cookie=remote.secure_cookie,
    )


# --- presets fill in values, from explicit options only -------------------------------


def test_ssh_allows_the_local_forward_port_only():
    r = container_access(port=PORT, access="ssh", local_port=9000)
    assert r.bind == CONTAINER_BIND
    assert r.public_url == "http://127.0.0.1:9000/"
    assert r.base_path == "/"
    assert r.hosts == {"127.0.0.1:9000", "localhost:9000"}
    assert r.origins == {"http://127.0.0.1:9000", "http://localhost:9000"}
    assert not r.secure_cookie


def test_ssh_local_port_defaults_to_the_container_port():
    assert container_access(port=PORT).public_url == f"http://127.0.0.1:{PORT}/"


@pytest.mark.parametrize("name", ["vscode", "jupyter", "workbench"])
def test_ide_presets_take_host_origin_and_prefix_from_the_url(name):
    r = container_access(port=PORT, **PRESETS[name])
    url = PRESETS[name]["public_url"]
    host = url.split("/")[2]
    assert r.public_url == url
    assert r.base_path == "/" + url.split("/", 3)[3]
    assert host in r.hosts
    assert f"https://{host}" in r.origins
    assert r.secure_cookie


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (dict(access="vscode"), "needs --public-url"),
        (dict(access="vscode", public_url="https://ide.example.com/elsewhere/"), "doesn't look"),
        (dict(access="jupyter", public_url="ftp://hub/proxy/1/"), "http(s) URL"),
        (dict(access="vscode", public_url="https://ide/proxy/1/?x=1"), "query"),
        (dict(access="ssh", public_url="https://ide/proxy/1/"), "ssh uses --local-port"),
        (dict(access="vscode", local_port=1, public_url="https://ide/proxy/1/"), "for ssh"),
        (dict(access="nope"), "--access must be"),
        (dict(access="ssh", allowed_origins=("https://a.example/path",)), "scheme://host"),
        (dict(access="ssh", allowed_hosts=("bad host",)), "host[:port]"),
    ],
)
def test_bad_options_are_refused(kwargs, message):
    with pytest.raises(RemoteAccessError, match=re.escape(message)):
        container_access(port=PORT, **kwargs)


def test_two_containers_get_different_cookie_names():
    a = container_access(port=PORT, access="ssh", local_port=9000)
    b = container_access(port=PORT, access="ssh", local_port=9001)
    assert security_for(a).cookie_name != security_for(b).cookie_name


def test_security_config_rejects_a_malformed_base_path():
    with pytest.raises(ValueError):
        SecurityConfig(port=1, base_path="proxy/1")


# --- every check still holds behind each access route ---------------------------------


# Every check runs for the container and for a native Linux VM (#65): the two differ in the
# bind address only, and the security checks must hold identically behind each access route.
MODES = {"container": container_access, "native": native_access}


@pytest.fixture(
    params=[(m, p) for m in MODES for p in sorted(PRESETS)], ids=lambda x: f"{x[0]}-{x[1]}"
)
def remote_client(request, tmp_path):
    mode, preset = request.param
    remote = MODES[mode](port=PORT, **PRESETS[preset])
    security = security_for(remote)
    app = create_app(security, open_tokens=tmp_path / "tokens")
    origin = "/".join(remote.public_url.split("/")[:3])
    with TestClient(app, base_url=origin, follow_redirects=False) as c:
        c.remote, c.security, c.tokens = remote, security, tmp_path / "tokens"
        c.prefix = remote.base_path
        c.host = remote.public_url.split("/")[2]
        c.origin = remote.public_url.split("/")[0] + "//" + c.host
        c.cookies.set(security.cookie_name, security.secret)
        yield c


def test_home_works_with_and_without_the_prefix(remote_client):
    c = remote_client
    assert c.get(c.prefix).status_code == 200  # a proxy that keeps the prefix
    assert c.get("/", headers={"host": c.host}).status_code == 200  # one that strips it


def test_the_proxys_rewritten_host_is_accepted(remote_client):
    c = remote_client
    assert c.get("/health", headers={"host": f"localhost:{PORT}"}).status_code == 200


def test_foreign_host_still_gets_400(remote_client):
    assert remote_client.get("/health", headers={"host": "evil.example"}).status_code == 400


def test_foreign_origin_still_gets_403(remote_client):
    c = remote_client
    headers = {SESSION_HEADER: c.security.secret, "origin": "https://evil.example"}
    assert c.post(c.prefix + "api/quit", headers=headers).status_code == 403


def test_missing_session_token_still_gets_403(remote_client):
    c = remote_client
    assert c.post(c.prefix + "api/quit", headers={"origin": c.origin}).status_code == 403


def test_missing_cookie_still_gets_403(remote_client):
    c = remote_client
    c.cookies.clear()
    assert c.get(c.prefix + "api/tools").status_code == 403


def test_same_origin_post_with_token_works(remote_client):
    c = remote_client
    headers = {SESSION_HEADER: c.security.secret, "origin": c.origin}
    assert c.post(c.prefix + "api/quit", headers=headers).status_code == 200


def test_open_sets_a_prefixed_cookie_and_redirects_under_the_prefix(remote_client):
    c = remote_client
    c.cookies.clear()
    token = mint_open_token(c.tokens)
    resp = c.get(f"{c.prefix}open?token={token}")
    assert resp.status_code == 303
    assert resp.headers["location"] == c.prefix
    cookie = resp.headers["set-cookie"]
    assert f"Path={c.prefix}" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert ("Secure" in cookie) == c.remote.secure_cookie


def test_children_see_the_prefixed_path(tmp_path):
    from opncafyr.core.runtime import Runtime

    rt = Runtime(source=tmp_path / "runtime.json", executables={}, r_library=None)
    sup = Supervisor(
        {}, runtime=rt, state_root=tmp_path, log_root=tmp_path, url_prefix="/proxy/8765/"
    )
    assert sup.child_path("/apps/opnca/x") == "/proxy/8765/apps/opnca/x"
    plain = Supervisor({}, runtime=rt, state_root=tmp_path, log_root=tmp_path)
    assert plain.child_path("/apps/opnca/") == "/apps/opnca/"


# --- storage ----------------------------------------------------------------------------

MOUNTINFO = """\
22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw
40 22 0:50 / /workspace rw - fuse.s3fs s3fs rw
41 22 0:51 / /mnt/efs rw - nfs4 fs-1.efs:/ rw
42 22 0:52 / /mnt/with\\040space rw - virtiofs share rw
"""


@pytest.mark.parametrize(
    ("path", "fstype"),
    [
        ("/workspace/study", "fuse.s3fs"),
        ("/mnt/efs/a", "nfs4"),
        ("/mnt/with space/x", "virtiofs"),
        ("/home/ana", "ext4"),
        ("/workspacefoo", "ext4"),
    ],
)
def test_mount_type_is_the_longest_containing_mount(path, fstype):
    assert storage.mount_type(Path(path), MOUNTINFO) == fstype


def _info(tmp_path, fstype):
    # A Linux mountinfo line naming the folder as check_storage resolves it (on Windows
    # too, where only these tests run it).
    mount_point = Path(os.path.realpath(tmp_path)).as_posix()
    return f"22 1 8:1 / {mount_point} rw - {fstype} dev rw\n"


def test_object_storage_fuse_is_refused(tmp_path):
    result = storage.check_storage(tmp_path, _info(tmp_path, "fuse.gcsfuse"))
    assert not result.ok
    assert "object storage" in result.message


def test_network_share_is_allowed_after_the_probe_with_a_note(tmp_path):
    result = storage.check_storage(tmp_path, _info(tmp_path, "nfs4"))
    assert result.ok and result.kind == "network"
    assert "network file share" in result.message
    assert list(tmp_path.iterdir()) == []  # the probe cleaned up


def test_a_failed_probe_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "rename_probe", lambda folder: False)
    result = storage.check_storage(tmp_path, _info(tmp_path, "cifs"))
    assert not result.ok and "safe-replace" in result.message


def test_local_disk_needs_no_note(tmp_path):
    result = storage.check_storage(tmp_path, _info(tmp_path, "ext4"))
    assert result.ok and result.message is None


def test_rename_probe_on_a_real_folder(tmp_path):
    assert storage.rename_probe(tmp_path)


# --- one fixed study folder ---------------------------------------------------------------


@pytest.fixture
def fixed_client(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    security = SecurityConfig(port=PORT)
    app = create_app(security, open_tokens=tmp_path / "tokens", workspace=study)
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        cookies={security.cookie_name: security.secret},
    ) as c:
        c.headers.update({SESSION_HEADER: security.secret})
        c.study = study.resolve()
        yield c


def test_choosing_a_folder_returns_the_fixed_one(fixed_client):
    c = fixed_client
    resp = c.post("/api/workspace/pick")
    assert resp.status_code == 200
    assert resp.json()["folder"]["path"] == str(c.study)
    state = c.get("/api/workspace").json()
    assert state["fixed"] == {"path": str(c.study), "name": "study"}
    assert state["recent"] == []


def test_no_other_folder_is_accepted(fixed_client, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    resp = fixed_client.post("/api/workspace/inspect", json={"path": str(other)})
    assert resp.status_code == 403


def test_the_fixed_folder_opens(fixed_client):
    c = fixed_client
    resp = c.post("/api/workspace/init", json={"path": str(c.study)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["workspace"] is not None


def test_unsupported_storage_is_refused_at_init(fixed_client, monkeypatch):
    c = fixed_client
    refused = storage.StorageCheck(storage.UNSUPPORTED, "fuse.s3fs", "object storage")
    monkeypatch.setattr("opncafyr.core.workspace_api.check_storage", lambda p: refused)
    resp = c.post("/api/workspace/init", json={"path": str(c.study)})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unsupported_filesystem"
    assert not (c.study / ".opncafyr").exists()


# --- the CLI only takes remote options together with --container --------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["launch", "--access", "ssh"],
        ["launch", "--public-url", "https://ide/proxy/1/"],
        ["launch", "--workspace", "."],
        ["launch", "--allowed-host", "example.com"],
    ],
)
def test_remote_options_need_container(argv, capsys):
    assert main(argv) == 2
    assert "only with --container" in capsys.readouterr().err


def test_container_needs_a_fixed_port(capsys):
    assert main(["launch", "--container"]) == 2
    assert "fixed --port" in capsys.readouterr().err


def test_container_refuses_bad_access_options(capsys):
    assert main(["launch", "--container", "--port", "8765", "--access", "vscode"]) == 2
    assert "needs --public-url" in capsys.readouterr().err


def test_container_image_must_be_an_image_id(capsys):
    argv = ["launch", "--container", "--port", "8765", "--container-image", "latest"]
    assert main(argv) == 2
    assert "image ID" in capsys.readouterr().err


def test_manifests_record_the_container_image():
    from opncafyr.core import provenance

    image = "sha256:" + "a" * 64
    provenance.set_container_image(image)
    try:
        assert provenance.base_tools()["container_image"] == image
        from opncafyr.workflows.nca.replay import replay_tools

        tools = replay_tools({"opnca-r": "0.3.1", "R": "4.6.2", "container_image": image, "x": "y"})
        assert tools == {
            "opnca-r": "0.3.1",
            "R": "4.6.2",
            "container_image": image,
            "replay_container_image": image,
        }
    finally:
        provenance.set_container_image(None)
    assert "container_image" not in provenance.base_tools()
    with pytest.raises(ValueError):
        provenance.set_container_image("ghcr.io/x:latest")


def test_a_folder_owned_by_someone_else_is_reported_without_a_path(tmp_path, monkeypatch):
    # Git refuses such a folder ("dubious ownership"); the container start says why (#62).
    if not hasattr(os, "geteuid"):
        pytest.skip("POSIX owners only")
    assert storage.owner_mismatch(tmp_path) is None
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(tmp_path).st_uid + 1)
    message = storage.owner_mismatch(tmp_path)
    assert message is not None
    assert "--user" in message and "opncafyr-container" in message
    assert str(tmp_path) not in message
    assert storage.owner_mismatch(tmp_path / "missing") is None


# --- a native Linux VM (#65) --------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_native_is_the_container_with_a_loopback_listener(name):
    inside = container_access(port=PORT, **PRESETS[name])
    native = native_access(port=PORT, **PRESETS[name])
    assert (inside.bind, native.bind) == (CONTAINER_BIND, LOOPBACK)
    assert not inside.native and native.native
    # Same allow-lists, prefix and URL: only the listener differs, so no check is weaker.
    assert (native.hosts, native.origins, native.base_path, native.public_url) == (
        inside.hosts,
        inside.origins,
        inside.base_path,
        inside.public_url,
    )


def test_native_refuses_what_the_container_refuses():
    with pytest.raises(RemoteAccessError, match="needs --public-url"):
        native_access(port=PORT, access="vscode")
    with pytest.raises(RemoteAccessError, match="--local-port is for ssh"):
        native_access(
            port=PORT,
            access="jupyter",
            local_port=9000,
            public_url=PRESETS["jupyter"]["public_url"],
        )


def test_remote_and_container_are_exclusive(capsys):
    assert main(["launch", "--container", "--remote", "--port", "8765"]) == 2
    assert "different modes" in capsys.readouterr().err


def test_remote_needs_a_fixed_port_and_a_folder(capsys, tmp_path):
    assert main(["launch", "--remote"]) == 2
    assert "--remote needs a fixed --port" in capsys.readouterr().err
    assert main(["launch", "--remote", "--port", "8765"]) == 2
    assert "no folder picker" in capsys.readouterr().err
    missing = str(tmp_path / "missing")
    assert main(["launch", "--remote", "--port", "8765", "--workspace", missing]) == 2
    assert "is not a folder" in capsys.readouterr().err


def test_remote_has_no_container_image(capsys, tmp_path):
    argv = ["launch", "--remote", "--port", "8765", "--workspace", str(tmp_path)]
    assert main([*argv, "--container-image", "sha256:" + "a" * 64]) == 2
    assert "only for --container" in capsys.readouterr().err


def test_remote_refuses_bad_access_options(capsys, tmp_path):
    argv = ["launch", "--remote", "--port", "8765", "--workspace", str(tmp_path)]
    assert main([*argv, "--access", "vscode"]) == 2
    assert "needs --public-url" in capsys.readouterr().err


def test_the_owner_warning_names_the_right_fix_for_each_mode(monkeypatch, tmp_path):
    from opncafyr.core import storage

    monkeypatch.setattr(storage.os, "geteuid", lambda: os.stat(tmp_path).st_uid + 1, raising=False)
    inside = storage.owner_mismatch(tmp_path)
    native = storage.owner_mismatch(tmp_path, container=False)
    assert "opncafyr-container" in inside and "opncafyr-container" not in native
    assert "Run opncafyr as the user who owns the folder" in native
