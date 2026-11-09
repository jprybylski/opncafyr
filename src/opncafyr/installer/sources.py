"""Where pinned files come from: the download cache, local folders or bundles, or the web.

``resolve`` returns a verified local path for every asset, or raises ``MissingAssets``
naming each component it couldn't satisfy. A best-effort asset (an optional component
installed by default, such as Git, #56) that can't be satisfied is reported in
``skipped`` instead, when the caller passes it. Every file is hashed against the pin
before it is returned, wherever it came from; a bundle's own hash list is never consulted.

- ``Cache`` keeps verified downloads under ``<root>/cache/downloads/<sha256>/<file>``.
- ``LocalSource`` reads a bundle folder or ``.zip`` (``assets/<component>/<file>``) or a
  plain ``--assets`` folder (files found by name). It has no way to reach the network.
- ``DownloadSource`` runs the system curl (``/usr/bin/curl``, ``curl.exe`` in System32), so
  downloads use the OS trust store and the user's proxy variables. Private GitHub release
  assets (opnca-r) go through the GitHub API with ``GH_TOKEN``/``GITHUB_TOKEN``, passed on
  curl's stdin, never on its command line. An optional ``rewrite`` maps each URL to an
  internal mirror (``mirror.toml``, #36).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import zipfile
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from opncafyr.installer import proc
from opncafyr.installer.pins import Asset
from opncafyr.installer.verify import AssetError, InstallError, MissingAssets, sha256_file

_GITHUB_RELEASE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/download/"
    r"(?P<tag>[^/]+)/(?P<name>[^/]+)$"
)
TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")

Progress = Callable[[str], None]


class Cache:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path(self, asset: Asset) -> Path:
        return self.directory / asset.sha256 / asset.file

    def get(self, asset: Asset) -> Path | None:
        path = self.path(asset)
        if path.is_file():
            if sha256_file(path) == asset.sha256:
                return path
            path.unlink()  # damaged cache entry: fetch again
        return None

    def put(self, asset: Asset, src: Path, *, move: bool = False) -> Path:
        dest = self.path(asset)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        if move:
            os.replace(src, tmp)
        else:
            shutil.copyfile(src, tmp)
        if sha256_file(tmp) != asset.sha256:
            tmp.unlink()
            raise AssetError(asset.component, asset.file, "SHA-256 does not match the pin")
        os.replace(tmp, dest)
        return dest


class LocalSource:
    """A bundle (folder or zip) or a folder of pinned files. Never touches the network."""

    def __init__(self, location: Path, cache: Cache) -> None:
        # A bundle may be passed as ".". Its files can become process executables
        # (the Windows R installer), which must have absolute paths.
        self.location = Path(location).resolve()
        self.cache = cache
        self._names: dict[str, list[Path]] | None = None
        self._zip: zipfile.ZipFile | None = None
        if not self.location.exists():
            raise FileNotFoundError(f"{self.location} does not exist")

    @property
    def label(self) -> str:
        return str(self.location)

    def find(self, asset: Asset) -> tuple[Path | None, str | None]:
        """(verified path, None) or (None, problem). Problem is None if simply absent."""
        if self.location.is_file() and zipfile.is_zipfile(self.location):
            return self._find_in_zip(asset)
        candidates = [self.location / asset.path, self.location / asset.file]
        candidates += self._by_name().get(asset.file, [])
        seen = set()
        problem = None
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            if sha256_file(path) == asset.sha256:
                return path, None
            problem = f"{path.name} in {self.label} does not match the pinned SHA-256"
        return None, problem

    def _by_name(self) -> dict[str, list[Path]]:
        if self._names is None:
            self._names = {}
            if self.location.is_dir():
                for path in self.location.rglob("*"):
                    if path.is_file():
                        self._names.setdefault(path.name, []).append(path)
        return self._names

    def _find_in_zip(self, asset: Asset) -> tuple[Path | None, str | None]:
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.location)
        names = self._zip.namelist()
        member = next(
            (
                n
                for n in names
                if n == asset.path or n.endswith("/" + asset.path) or n == asset.file
            ),
            None,
        )
        if member is None:
            member = next((n for n in names if n.rsplit("/", 1)[-1] == asset.file), None)
        if member is None:
            return None, None
        tmp = self.cache.path(asset).with_name(asset.file + ".zip-part")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with self._zip.open(member) as src, tmp.open("wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        try:
            return self.cache.put(asset, tmp, move=True), None
        except AssetError:
            tmp.unlink(missing_ok=True)
            return None, f"{member} in {self.label} does not match the pinned SHA-256"

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


class DownloadSource:
    def __init__(
        self,
        cache: Cache,
        *,
        log: Path,
        cacert: Path | None = None,
        proxy: str | None = None,
        rewrite: Callable[[str], str] | None = None,
        jobs: int = 6,
        environ: Mapping[str, str] | None = None,
        user_agent: str = "opncafyr-installer",
    ) -> None:
        self.cache = cache
        self.log = log
        self.cacert = cacert
        self.proxy = proxy
        self.rewrite = rewrite
        self.jobs = jobs
        self.environ = dict(os.environ if environ is None else environ)
        self.user_agent = user_agent
        self.curl = _curl()
        self._lock = threading.Lock()
        self._releases: dict[tuple[str, str, str], Any] = {}

    @property
    def token(self) -> str | None:
        return next((self.environ[k] for k in TOKEN_ENV if self.environ.get(k)), None)

    def _argv(self, url: str, out: Path, user_agent: str = "") -> list[str]:
        argv = [self.curl, "--silent", "--show-error", "--fail", "--location"]
        argv += ["--proto", "=https", "--proto-redir", "=https"]
        argv += ["--retry", "3", "--retry-delay", "2", "--connect-timeout", "30"]
        # A Linux R binary is served only to R's own User-Agent (#65); the pins name it.
        argv += ["--user-agent", user_agent or self.user_agent, "--output", str(out)]
        if sys.platform == "win32":
            argv.append("--ssl-revoke-best-effort")  # revocation checks fail behind proxies
        if self.cacert is not None:
            argv += ["--cacert", str(self.cacert)]
        if self.proxy:
            argv += ["--proxy", self.proxy]
        if self.rewrite is not None:
            argv.append("--netrc-optional")  # mirror credentials, if any, from ~/.netrc
        return [*argv, url]

    def _env(self) -> dict[str, str]:
        return proc.base_env(offline=False, environ=self.environ)

    def _config(self, headers: Iterable[str]) -> bytes:
        return "".join(f'header = "{h}"\n' for h in headers).encode()

    def download(self, asset: Asset) -> Path:
        dest = self.cache.path(asset)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".download")
        part.unlink(missing_ok=True)
        try:
            url = self.rewrite(asset.url) if self.rewrite else asset.url
        except InstallError as exc:  # MirrorError: no mirror for this origin
            raise AssetError(asset.component, asset.file, str(exc)) from None
        headers: list[str] = []
        if asset.private and _GITHUB_RELEASE.match(url):
            url, headers = self._github_asset(asset, url)
        elif asset.private:
            pass  # a mirror serves private files like any other
        argv = self._argv(url, part, asset.user_agent)
        if headers:
            argv[1:1] = ["--config", "-"]
        try:
            proc.run(
                argv,
                env=self._env(),
                log=self.log,
                stdin=self._config(headers) if headers else None,
                timeout=3600,
            )
        except proc.ProcError as exc:
            part.unlink(missing_ok=True)
            raise AssetError(asset.component, asset.file, f"download failed: {exc}") from None
        return self.cache.put(asset, part, move=True)

    def _github_asset(self, asset: Asset, url: str) -> tuple[str, list[str]]:
        token = self.token
        if not token:
            raise AssetError(
                asset.component,
                asset.file,
                "is a private release asset: set GH_TOKEN, or pass --assets <folder> "
                "holding this file",
            )
        m = _GITHUB_RELEASE.match(url)
        if m is None:
            raise AssetError(asset.component, asset.file, f"not a GitHub release URL: {url}")
        auth = f"Authorization: Bearer {token}"
        release = self._release(m["owner"], m["repo"], m["tag"], auth, asset)
        try:
            api_url = next(a["url"] for a in release["assets"] if a["name"] == asset.file)
        except (KeyError, StopIteration, TypeError):
            raise AssetError(asset.component, asset.file, "not found in the release") from None
        return api_url, [auth, "Accept: application/octet-stream"]

    def _release(self, owner: str, repo: str, tag: str, auth: str, asset: Asset) -> Any:
        """The release's asset listing, fetched once (downloads run in parallel)."""
        key = (owner, repo, tag)
        with self._lock:
            if key in self._releases:
                return self._releases[key]
            self.cache.directory.mkdir(parents=True, exist_ok=True)
            listing = self.cache.directory / f".release-{owner}-{repo}-{tag}.json.download"
            listing.unlink(missing_ok=True)
            api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
            argv = self._argv(api, listing)
            argv[1:1] = ["--config", "-"]
            try:
                proc.run(
                    argv,
                    env=self._env(),
                    log=self.log,
                    stdin=self._config([auth, "Accept: application/vnd.github+json"]),
                    timeout=120,
                )
                release = json.loads(listing.read_text("utf-8"))
            except (proc.ProcError, OSError, ValueError) as exc:
                raise AssetError(
                    asset.component, asset.file, f"GitHub release lookup failed: {exc}"
                ) from None
            finally:
                listing.unlink(missing_ok=True)
            if not isinstance(release, dict) or "assets" not in release:
                message = release.get("message") if isinstance(release, dict) else ""
                raise AssetError(
                    asset.component, asset.file, f"GitHub release lookup failed: {message}"
                )
            self._releases[key] = release
            return release


def resolve(
    assets: Iterable[Asset],
    *,
    cache: Cache,
    local: Iterable[LocalSource] = (),
    download: DownloadSource | None = None,
    progress: Progress | None = None,
    skipped: dict[str, str] | None = None,
) -> dict[str, Path]:
    """A verified local file for every asset, keyed by ``Asset.path``.

    With ``skipped``, a best-effort asset that can't be found or downloaded doesn't fail:
    its component is added to ``skipped`` (component -> problem) and it has no entry.
    """
    say = progress or (lambda _msg: None)
    found: dict[str, Path] = {}
    errors: list[AssetError] = []
    wanted: list[Asset] = []
    local = list(local)
    assets = list(assets)
    soft = {(a.component, a.file) for a in assets if a.best_effort and skipped is not None}
    for asset in assets:
        path = cache.get(asset)
        problem = None
        for source in local:
            if path is not None:
                break
            path, problem = source.find(asset)
        if path is not None:
            found[asset.path] = path
        elif download is not None:
            wanted.append(asset)
        else:
            errors.append(
                AssetError(asset.component, asset.file, problem or "missing from the bundle")
            )
    if wanted and download is not None:
        say(f"downloading {len(wanted)} file(s)")
        with ThreadPoolExecutor(max_workers=download.jobs) as pool:
            futures = {pool.submit(download.download, a): a for a in wanted}
            for n, future in enumerate(as_completed(futures), 1):
                asset = futures[future]
                try:
                    found[asset.path] = future.result()
                    if n % 20 == 0 or n == len(wanted):
                        say(f"downloaded {n}/{len(wanted)}")
                except AssetError as exc:
                    errors.append(exc)
    hard = [e for e in errors if (e.component, e.file) not in soft]
    if hard:
        raise MissingAssets(hard)
    for e in errors:
        if skipped is not None and (e.component, e.file) in soft:
            problem = f"{e.file}: {e.problem}"
            skipped[e.component] = "; ".join(filter(None, [skipped.get(e.component), problem]))
    return found


def _curl() -> str:
    if sys.platform == "win32":
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return str(Path(root) / "System32" / "curl.exe")
    return "/usr/bin/curl"
