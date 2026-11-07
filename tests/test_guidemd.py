"""The user guide's Markdown parser (#40): ``web/static/js/guidemd.js`` under Node.

Skipped when Node isn't installed. ``tests/ui`` checks the rendered page.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "src/opncafyr/web/static/js/guidemd.js"
GUIDE = Path(__file__).resolve().parents[1] / "docs" / "user-guide"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")


def _node(expr: str, text: str):
    script = (
        f"import * as m from {json.dumps(MODULE.as_uri())};"
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>"
        f"process.stdout.write(JSON.stringify(({expr})(s))));"
    )
    out = subprocess.run(  # noqa: S603 - fixed argv, test only
        [NODE, "--input-type=module", "-e", script],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def parse(text: str):
    return _node("m.parse", text)


def test_blocks():
    md = (
        "# Title\n\n> **Research use only.** Not validated.\n\nOne line\ncontinued.\n\n"
        "| A | B |\n|---|---|\n| 1 | `a\\|b` |\n\n![Home page](images/01-home.png)\n\n"
        "```sh\necho hi\n```\n## 2. Next step\n"
    )
    blocks = parse(md)
    assert [b["type"] for b in blocks] == [
        "heading",
        "quote",
        "para",
        "table",
        "image",
        "code",
        "heading",
    ]
    assert blocks[1]["text"] == "**Research use only.** Not validated."
    assert blocks[2]["text"] == "One line continued."
    assert blocks[3] == {"type": "table", "header": ["A", "B"], "rows": [["1", "`a|b`"]]}
    assert blocks[4] == {"type": "image", "alt": "Home page", "src": "images/01-home.png"}
    assert blocks[5] == {"type": "code", "lang": "sh", "text": "echo hi"}
    assert blocks[6]["id"] == "2-next-step"


def test_nested_and_wrapped_lists():
    md = "1. First\n   - sub a\n   - sub b\n2. Second\n   wrapped\n\n- x\n- y\n"
    first, second = parse(md)
    assert first["ordered"] is True and [i["text"] for i in first["items"]] == [
        "First",
        "Second wrapped",
    ]
    assert [i["text"] for i in first["items"][0]["children"][0]["items"]] == ["sub a", "sub b"]
    assert second["ordered"] is False and len(second["items"]) == 2


def test_inline_tokens():
    tokens = _node("m.inline", "a **b** *c* `d` [e](f.md#g) \\*h")
    assert tokens == [
        {"type": "text", "text": "a "},
        {"type": "strong", "children": [{"type": "text", "text": "b"}]},
        {"type": "text", "text": " "},
        {"type": "em", "children": [{"type": "text", "text": "c"}]},
        {"type": "text", "text": " "},
        {"type": "code", "text": "d"},
        {"type": "text", "text": " "},
        {"type": "link", "href": "f.md#g", "children": [{"type": "text", "text": "e"}]},
        {"type": "text", "text": " *h"},
    ]


def test_raw_html_stays_text():
    (block,) = parse("<script>alert(1)</script>\n")
    assert block == {"type": "para", "text": "<script>alert(1)</script>"}


@pytest.mark.skipif(not (GUIDE / "index.md").is_file(), reason="no guide yet")
def test_every_guide_page_parses():
    for page in sorted(GUIDE.glob("*.md")):
        blocks = parse(page.read_text("utf-8"))
        assert blocks and blocks[0]["type"] == "heading" and blocks[0]["level"] == 1, page.name
