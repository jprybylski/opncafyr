"""Remote access for cloud container mode (#42, ADR 0009).

On a desktop the launcher listens on 127.0.0.1 and the browser is on the same machine.
In cloud container mode the launcher runs in a container on a VM, and the browser reaches
it through an SSH port forward or an IDE's authenticated proxy (VS Code Server or
code-server, Posit Workbench, jupyter-server-proxy). Then:

- the container's listener has to accept Docker's port forward, so it binds the container
  interface; the launch script publishes it to the VM's loopback only;
- the browser's ``Host`` and ``Origin`` are the forward's or the proxy's, not ours;
- behind an IDE proxy every URL sits under a prefix such as ``/proxy/8765/``.

Every one of these comes from explicit ``opncafyr launch`` options, **never** from
environment variables or anything else sniffed at run time. A preset only fills in the
allow-lists and the prefix from a URL the user gives; it never turns a check off. The
session cookie, the one-time ``/open`` token, the session header and every Host, Origin
and ``Sec-Fetch-Site`` check stay exactly as on a desktop (``core.security``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# The listener inside the container. Docker publishes it to the VM's 127.0.0.1 only
# (container/opncafyr-container); the container's own network is private to it.
CONTAINER_BIND = "0.0.0.0"  # noqa: S104 - container mode only, see module docstring
LOOPBACK = "127.0.0.1"

ACCESS_SSH = "ssh"
# What each IDE proxy's URL for a port looks like. The prefix ends in "/" and may or may
# not be stripped by the proxy before the request reaches us; both work.
PROXY_PATHS = {
    "vscode": re.compile(r"/(abs)?proxy/\d+/$"),  # VS Code Server, code-server
    "jupyter": re.compile(r"/proxy/(absolute/)?\d+/$"),  # jupyter-server-proxy
    "workbench": re.compile(r"/s/[^/]+/p/[^/]+/$"),  # Posit Workbench
}
ACCESS_CHOICES = (ACCESS_SSH, *PROXY_PATHS)
PROXY_EXAMPLES = {
    "vscode": "https://<ide host>/proxy/<port>/",
    "jupyter": "https://<hub host>/user/<name>/proxy/<port>/",
    "workbench": "https://<workbench host>/s/<session>/p/<id>/",
}
_AUTHORITY = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$")


class RemoteAccessError(ValueError):
    pass


@dataclass(frozen=True)
class RemoteAccess:
    """Where the browser reaches this launcher from, and what it may send."""

    access: str
    bind: str
    public_url: str  # the URL the user opens, ending in base_path
    base_path: str = "/"
    hosts: frozenset[str] = field(default_factory=frozenset)
    origins: frozenset[str] = field(default_factory=frozenset)

    @property
    def secure_cookie(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def cookie_id(self) -> str:
        """Cookies ignore ports, and every container listens on the same port inside, so
        the cookie is named after the public URL instead."""
        return hashlib.sha256(self.public_url.encode()).hexdigest()[:12]


def _origin(scheme: str, authority: str) -> str:
    return f"{scheme}://{authority}"


def _check_authority(value: str, what: str) -> str:
    if not _AUTHORITY.match(value):
        raise RemoteAccessError(f"{what} {value!r} is not a host[:port].")
    return value.lower()


def _check_origin(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise RemoteAccessError(f"--allowed-origin {value!r} must be scheme://host[:port].")
    return _origin(parts.scheme, _check_authority(parts.netloc, "--allowed-origin"))


def container_access(
    *,
    port: int,
    access: str = ACCESS_SSH,
    public_url: str | None = None,
    local_port: int | None = None,
    allowed_hosts: tuple[str, ...] = (),
    allowed_origins: tuple[str, ...] = (),
) -> RemoteAccess:
    """Settings for ``opncafyr launch --container``, from its options only.

    ``port`` is the launcher's port inside the container. For ``ssh``, ``local_port`` is
    the port on the user's own machine (``ssh -L <local_port>:127.0.0.1:<VM port>``); it
    defaults to ``port``. For an IDE proxy, ``public_url`` is the URL the IDE shows for
    the port.
    """
    hosts = {_check_authority(h, "--allowed-host") for h in allowed_hosts}
    origins = {_check_origin(o) for o in allowed_origins}
    if access == ACCESS_SSH:
        if public_url is not None:
            raise RemoteAccessError("--public-url is for IDE proxies; ssh uses --local-port.")
        lp = local_port or port
        if not 0 < lp < 65536:
            raise RemoteAccessError(f"--local-port {lp} is not a port.")
        for name in (LOOPBACK, "localhost"):
            hosts.add(f"{name}:{lp}")
            origins.add(_origin("http", f"{name}:{lp}"))
        return RemoteAccess(
            access=access,
            bind=CONTAINER_BIND,
            public_url=f"http://{LOOPBACK}:{lp}/",
            hosts=frozenset(hosts),
            origins=frozenset(origins),
        )
    if access not in PROXY_PATHS:
        raise RemoteAccessError(f"--access must be one of {', '.join(ACCESS_CHOICES)}.")
    if local_port is not None:
        raise RemoteAccessError("--local-port is for ssh; IDE proxies use --public-url.")
    if not public_url:
        raise RemoteAccessError(
            f"--access {access} needs --public-url, the URL the IDE shows for the port, "
            f"like {PROXY_EXAMPLES[access]}"
        )
    parts = urlsplit(public_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise RemoteAccessError(f"--public-url {public_url!r} must be an http(s) URL.")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise RemoteAccessError("--public-url must not have a query, fragment or user.")
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    if not PROXY_PATHS[access].search(path):
        raise RemoteAccessError(
            f"--public-url doesn't look like a {access} proxy URL ({PROXY_EXAMPLES[access]})."
        )
    authority = _check_authority(parts.netloc, "--public-url host")
    hosts.add(authority)
    origins.add(_origin(parts.scheme, authority))
    # Proxies forward with Host rewritten to the port they reach; that is our own
    # loopback authority, which core.security always allows.
    return RemoteAccess(
        access=access,
        bind=CONTAINER_BIND,
        public_url=f"{parts.scheme}://{authority}{path}",
        base_path=path,
        hosts=frozenset(hosts),
        origins=frozenset(origins),
    )
