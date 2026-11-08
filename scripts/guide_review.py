"""Remind a change to review both user guides (#53). Advisory: it warns, never fails.

opncafyr has two user guides: the in-app Guide (``docs/user-guide/``, shipped with the app
and readable offline) and the docs website (``site/``). A change to what users see or do
(screens, installer, commands, supported platforms) must be checked against both, in the
same change or a linked follow-up (``site/README.md``, "Keeping both guides current").

    uv run python scripts/guide_review.py --base origin/main     # local, before pushing
    uv run python scripts/guide_review.py --base <sha>           # CI (docs.yml)

It reports two things:

1. **Guides not reviewed:** files under ``WATCHED`` changed since ``--base`` but nothing
   under ``docs/user-guide/`` or nothing under ``site/`` did.
2. **Screenshots older than the UI:** files under ``UI`` changed in commits after the
   last commit that touched ``docs/user-guide/images/`` (rerun
   ``scripts/guide_screenshots.py``; the website copies the same images).

On GitHub Actions each finding is a ``::warning::`` annotation and the job summary gets
a checklist. Needs the git history (``fetch-depth: 0``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

GUIDES = {"in-app Guide": "docs/user-guide/", "docs website": "site/"}
# What users see or do. A change here can make either guide wrong.
WATCHED = (
    "installer/",
    "src/opncafyr/installer/",
    "src/opncafyr/web/static/",
    "src/opncafyr/workflows/nca/web/",
    "src/opncafyr/cli.py",
    "src/opncafyr/core/support.py",
    "runtime/pins.toml",
)
# What the screenshots show.
UI = ("src/opncafyr/web/static/", "src/opncafyr/workflows/nca/web/")
SCREENSHOTS = "docs/user-guide/images/"
CHECKLIST = """\
- [ ] Steps, labels and messages in `docs/user-guide/` still match the app
- [ ] The same pages on the website (`site/`) still match
- [ ] Screenshots: rerun `scripts/guide_screenshots.py` if a pictured screen changed
- [ ] Install and platform pages match the installer and `runtime/pins.toml`
- [ ] Research-use wording unchanged; no claim of clinical or regulatory validation
"""


def git(*args: str) -> list[str]:
    out = subprocess.run(  # noqa: S603 - dev tool, fixed argv
        ["git", *args],  # noqa: S607 - git from PATH, as in CI
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def under(paths: list[str], prefixes: tuple[str, ...] | str) -> list[str]:
    prefixes = (prefixes,) if isinstance(prefixes, str) else prefixes
    return [p for p in paths if p.startswith(prefixes) or p in prefixes]


def findings(base: str) -> list[str]:
    out = []
    changed = git("diff", "--name-only", f"{base}...HEAD")
    watched = under(changed, WATCHED)
    if watched:
        missing = [name for name, path in GUIDES.items() if not under(changed, path)]
        if missing:
            shown = ", ".join(watched[:5]) + (" …" if len(watched) > 5 else "")
            out.append(
                f"User-facing files changed ({shown}) but the {' and the '.join(missing)} "
                "did not. Review both guides in this change or a linked follow-up."
            )
    capture = git("log", "-1", "--format=%H", "--", SCREENSHOTS)
    if capture:
        newer = git("diff", "--name-only", capture[0], "HEAD", "--", *UI)
        if newer:
            shown = ", ".join(newer[:5]) + (" …" if len(newer) > 5 else "")
            out.append(
                f"The UI changed after the guide screenshots were captured ({shown}). "
                "If a pictured screen changed, rerun scripts/guide_screenshots.py."
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="the commit or ref to compare with")
    args = parser.parse_args(argv)
    try:
        found = findings(args.base)
    except subprocess.CalledProcessError as exc:
        print(f"guide review skipped: git failed ({exc.stderr.strip()})", file=sys.stderr)
        return 0
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for text in found:
        print(f"::warning title=Review both guides::{text}" if actions else f"warning: {text}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if actions and summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Guide review\n\n")
            fh.write("".join(f"- {t}\n" for t in found) or "Nothing to review.\n")
            if found:
                fh.write("\n" + CHECKLIST)
    if not found:
        print("guide review: nothing to review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
