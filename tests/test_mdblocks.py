"""The editor's visual mode is lossless (#52): ``web/static/js/mdblocks.js`` under Node.

Skipped when Node isn't installed. The browser checks (``tests/ui``) cover the preview.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from opncafyr.adapters import quartifyr as Q

MODULE = Path(__file__).resolve().parents[1] / "src/opncafyr/web/static/js/mdblocks.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")

# Constructs from quartifyr's demo report (examples/demo-report/report.qmd): YAML anchors
# and aliases, a chunk, shortcodes, custom divs, citations, cross-references, a table.
RICH = """---
title: "Demo"
project: &project "ABC-123"
report_number: *project
contributors:
  - name: A
    role: Author
bibliography: references.bib
format: docx
---

::: {custom-style="Heading 1"}
Synopsis
:::

{{< pagebreak >}}

# Introduction {#sec-intro}

Plain prose with *emphasis*, **strong** text and a [link](https://example.org).
It spans two lines.

- first item
- second item

```{r}
#| label: setup
x <- 1
```

As shown by @fig-conc and [@smith2020], the value is `r x`.

$$
AUC = \\int C\\,dt
$$

| A | B |
|---|---|
| 1 | 2 |

<div>raw</div>

::: {.callout-note}
Inner prose stays locked.

```python
print(":::")
```
:::

{rpfy}:table.csv

Last paragraph without a final newline"""


def run(script: str, data: dict) -> dict:
    code = f"""
import * as md from {json.dumps(MODULE.as_uri())};
const data = JSON.parse(process.argv[1]);
{script}
"""
    out = subprocess.run(  # noqa: S603
        [NODE, "--input-type=module", "-e", code, json.dumps(data)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(out.stdout)


def generated_report() -> str:
    items = [Q.ReportItem("t.csv", "table", "Summary", "Drug A", "20260101T000000Z-aaaa")]
    rows = [{"run_id": "20260101T000000Z-aaaa", "analyte": "Drug A"}]
    return Q.report_source(items, "pub-20260101T000000Z-aaaa", None, rows) + "\n"


PARSE = """
const blocks = md.parse(data.text);
console.log(JSON.stringify({
  joined: md.join(blocks) === data.text,
  blocks: blocks.map((b) => ({
    kind: b.kind, reason: b.reason || null, type: b.type || null, text: b.text,
  })),
}));
"""


@pytest.mark.parametrize("text", [RICH, generated_report(), "", "\n\n", "one", "# h"])
def test_blocks_join_back_to_the_exact_source(text):
    assert run(PARSE, {"text": text})["joined"]


def test_everything_but_plain_prose_is_locked():
    blocks = run(PARSE, {"text": RICH})["blocks"]
    prose = [b["text"] for b in blocks if b["kind"] == "prose"]
    assert prose == [
        "Plain prose with *emphasis*, **strong** text and a [link](https://example.org).\n"
        "It spans two lines.\n",
        "- first item\n- second item\n",
        "Last paragraph without a final newline",
    ]
    reasons = {b["reason"] for b in blocks if b["kind"] == "locked"}
    for expected in (
        "Front matter (YAML)",
        "Custom div (:::)",
        "Shortcode",
        "Code chunk",
        "Attributes or placeholder in braces",
        "Inline code",
        "Maths",
        "Table",
        "Raw HTML",
    ):
        assert expected in reasons
    # The whole callout div, including its inner fence with ":::", is one locked block.
    div = next(b for b in blocks if b["text"].startswith("::: {.callout-note}"))
    assert div["text"].endswith('print(":::")\n```\n:::\n')


EDIT = """
const blocks = md.parse(data.text);
const index = blocks.findIndex((b) => b.kind === "prose");
const out = data.values.map((v) => md.replaceBlock(blocks, index, v));
console.log(JSON.stringify({
  old: blocks[index].text,
  results: out.map((r) => r.ok ? {ok: true, text: r.text} : {ok: false, reason: r.reason}),
}));
"""


def test_a_visual_edit_changes_only_its_own_block():
    out = run(EDIT, {"text": RICH, "values": ["Edited *prose*.\n\nA second paragraph."]})
    result = out["results"][0]
    assert result["ok"]
    old = out["old"]
    start = RICH.index(old)
    expected = RICH[:start] + "Edited *prose*.\n\nA second paragraph.\n" + RICH[start + len(old) :]
    assert result["text"] == expected


@pytest.mark.parametrize(
    "value",
    [
        "Text with ```",
        "x\n\n```{r}\nsystem('rm -rf /')\n```",
        "A {{< pagebreak >}} here",
        "::: {.x}",
        "See @fig-a",
        "Inline `r 1+1`",
        "Math $x$",
        "<b>bold</b>",
        "Attr {#id}",
        "   ",
    ],
)
def test_visual_edits_that_add_locked_syntax_are_refused(value):
    out = run(EDIT, {"text": RICH, "values": [value]})
    assert out["results"][0]["ok"] is False


def test_an_edit_that_would_change_a_later_locked_block_is_refused():
    text = "Para.\n```\ncode\n```\n"
    # Opening a fence in the paragraph would swallow the code block that follows.
    out = run(EDIT, {"text": text, "values": ["Para.\n~~~"]})
    assert out["results"][0]["ok"] is False
