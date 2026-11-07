// Markdown / Quarto / R Markdown split into exact source blocks, for the editor's visual
// mode and preview (#52). Nothing here runs or interprets document code.
//
// parse(text) cuts the text into blocks whose `text` values join back to the input
// exactly. A block is one of:
//   - "prose": an ordinary heading, paragraph or list whose inline syntax is only
//     emphasis, strong text, links and backslash escapes. Visual mode may edit these.
//   - "locked": everything else, kept source-exact with a `reason`: front matter, code
//     fences and chunks, ::: divs, raw HTML, shortcodes, attributes and placeholders in
//     braces, inline code, citations and cross-references, maths, tables, images and any
//     syntax not listed above.
//   - "blank": blank lines between blocks.
// An edit is accepted only if the edited block still parses as prose and every locked
// block of the document is unchanged (lockedSignature), so visual mode can never
// transform a region it doesn't understand.

const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const DIV = /^ {0,3}(:{3,})(.*)$/;
const HEADING = /^#{1,6}(\s|$)/;
const LIST_ITEM = /^\s{0,3}([-*+]|\d{1,9}[.)])\s+\S/;
const BLANK = /^[ \t]*$/;

function lines(text) {
  // Each line keeps its "\n"; the last may have none.
  const out = text.match(/[^\n]*\n|[^\n]+$/g);
  return out || [];
}

const strip = (line) => line.replace(/\n$/, "");

// Why a run of ordinary-looking lines can't be edited visually, or null when it can.
export function lockReason(text) {
  const body = text.replace(/\n$/, "");
  const all = body.split("\n");
  if (/\{\{[<%]/.test(body)) return "Shortcode";
  if (/[{}]/.test(body)) return "Attributes or placeholder in braces";
  if (/`/.test(body)) return "Inline code";
  if (/(^|[\s[(;])-?@/.test(body)) return "Citation or cross-reference";
  if (/\$/.test(body)) return "Maths";
  if (/</.test(body)) return "Raw HTML or autolink";
  if (/!\[/.test(body)) return "Image";
  if (/\[\^/.test(body)) return "Footnote";
  if (/\]\[/.test(body)) return "Link reference";
  if (all.some((l) => /^\s*\[[^\]]+\]:/.test(l))) return "Link reference";
  if (all.some((l) => /^\s*\|/.test(l) || /\|\s*$/.test(l))) return "Table";
  if (all.some((l) => /^\s*>/.test(l))) return "Block quote";
  if (all.some((l) => /^\s*[=\-*_]{3,}\s*$/.test(l))) return "Rule or underlined heading";
  if (all.some((l) => /^(\t| {4,})\S/.test(l)) && !LIST_ITEM.test(all[0])) return "Indented code";
  if (all.some((l) => /^\s*[:~^]/.test(l))) return "Definition list or other syntax";
  if (all.slice(1).some((l) => HEADING.test(l))) return "Heading inside a paragraph";
  if (HEADING.test(all[0]) && all.length > 1) return "Heading with more lines";
  return null;
}

function proseType(text) {
  if (HEADING.test(text)) return "heading";
  if (LIST_ITEM.test(text)) return "list";
  return "paragraph";
}

// The line after the fence that opens at `start` closes (or the end of the text).
function fenceEnd(src, start, mark) {
  let j = start + 1;
  while (j < src.length) {
    const close = FENCE.exec(strip(src[j]));
    j++;
    if (close && close[1][0] === mark[0] && close[1].length >= mark.length && !close[2].trim()) break;
  }
  return j;
}

export function parse(text) {
  const src = lines(text);
  const blocks = [];
  let i = 0;
  const push = (kind, from, to, extra) => {
    blocks.push({ kind, text: src.slice(from, to).join(""), ...extra });
  };

  // Front matter: a first line "---" up to the next "---" or "...".
  if (src.length && strip(src[0]) === "---") {
    let j = 1;
    while (j < src.length && !/^(---|\.\.\.)\s*$/.test(strip(src[j]))) j++;
    const end = Math.min(j + 1, src.length);
    push("locked", 0, end, { reason: "Front matter (YAML)" });
    i = end;
  }

  while (i < src.length) {
    const line = strip(src[i]);
    if (BLANK.test(line)) {
      let j = i;
      while (j < src.length && BLANK.test(strip(src[j]))) j++;
      push("blank", i, j);
      i = j;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const j = fenceEnd(src, i, fence[1]);
      const reason = fence[2].trim().startsWith("{") ? "Code chunk" : "Code block";
      push("locked", i, j, { reason });
      i = j;
      continue;
    }
    if (DIV.test(line)) {
      let depth = 0;
      let j = i;
      while (j < src.length) {
        const fenced = FENCE.exec(strip(src[j]));
        if (fenced && j > i) {
          j = fenceEnd(src, j, fenced[1]);
          continue;
        }
        const div = DIV.exec(strip(src[j]));
        j++;
        if (div) {
          if (div[2].trim()) depth++;
          else depth--;
          if (depth <= 0) break;
        }
      }
      push("locked", i, j, { reason: "Custom div (:::)" });
      i = j;
      continue;
    }
    if (HEADING.test(line)) {
      const reason = lockReason(src[i]);
      push(reason ? "locked" : "prose", i, i + 1, reason ? { reason } : { type: "heading" });
      i++;
      continue;
    }
    // A run of lines up to a blank line or the start of another kind of block.
    let j = i + 1;
    while (j < src.length) {
      const next = strip(src[j]);
      if (BLANK.test(next) || FENCE.test(next) || DIV.test(next) || HEADING.test(next)) break;
      j++;
    }
    const chunk = src.slice(i, j).join("");
    const reason = /^\s{0,3}</.test(line) ? "Raw HTML" : lockReason(chunk);
    push(reason ? "locked" : "prose", i, j, reason ? { reason } : { type: proseType(chunk) });
    i = j;
  }
  return blocks;
}

export const join = (blocks) => blocks.map((b) => b.text).join("");

export const lockedSignature = (blocks) =>
  JSON.stringify(blocks.filter((b) => b.kind === "locked").map((b) => b.text));

// The editable text of a prose block (its final line end is kept aside).
export const editable = (block) => block.text.replace(/\n$/, "");

// Replace block `index` with `value` (as typed in visual mode). Returns
// { ok, blocks, text } or { ok: false, reason }. Locked blocks never change.
export function replaceBlock(blocks, index, value) {
  const old = blocks[index];
  if (!old || old.kind !== "prose") return { ok: false, reason: "Only plain text can be edited here." };
  if (value.includes("\r")) value = value.replace(/\r\n?/g, "\n");
  const text = value + (old.text.endsWith("\n") ? "\n" : "");
  if (!value.trim()) return { ok: false, reason: "A block can't be empty here; delete it in source mode." };
  const own = parse(text);
  const bad = own.find((b) => b.kind === "locked");
  if (bad) return { ok: false, reason: `This adds syntax visual mode keeps locked (${bad.reason.toLowerCase()}). Use Source mode.` };
  const next = blocks.map((b, i) => (i === index ? { ...b, text } : b));
  const joined = join(next);
  if (lockedSignature(parse(joined)) !== lockedSignature(blocks)) {
    return { ok: false, reason: "This would change how a locked part is read. Use Source mode." };
  }
  return { ok: true, blocks: next, text: joined };
}

// --- preview (DOM only, no innerHTML; links are inert) ----------------------------------

function inline(text, parent) {
  let buf = "";
  const flush = () => {
    if (buf) parent.append(document.createTextNode(buf));
    buf = "";
  };
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "\\" && text[i + 1] === "\n") {
      flush();
      parent.append(document.createElement("br"));
      i += 2;
      continue;
    }
    if (c === "\\" && i + 1 < text.length && /[!-/:-@[-`{-~]/.test(text[i + 1])) {
      buf += text[i + 1];
      i += 2;
      continue;
    }
    if (c === "\n") {
      buf += " ";
      i++;
      continue;
    }
    if (c === "[") {
      const m = /^\[([^\]]*)\]\(([^)\s]*)(?:\s+"[^"]*")?\)/.exec(text.slice(i));
      if (m) {
        flush();
        const a = document.createElement("span");
        a.className = "md-link";
        a.title = m[2];
        inline(m[1], a);
        parent.append(a);
        i += m[0].length;
        continue;
      }
    }
    if (c === "*" || c === "_") {
      const n = text[i + 1] === c ? 2 : 1;
      const delim = c.repeat(n);
      const close = text.indexOf(delim, i + n);
      if (close > i + n) {
        flush();
        const node = document.createElement(n === 2 ? "strong" : "em");
        inline(text.slice(i + n, close), node);
        parent.append(node);
        i = close + n;
        continue;
      }
    }
    buf += c;
    i++;
  }
  flush();
}

function renderProse(block) {
  const body = editable(block);
  if (block.type === "heading") {
    const m = /^(#{1,6})\s*(.*?)\s*#*\s*$/.exec(body);
    const h = document.createElement(`h${Math.min(6, m[1].length + 1)}`);
    inline(m[2], h);
    return h;
  }
  if (block.type === "list") {
    const ordered = /^\s*\d/.test(body);
    const list = document.createElement(ordered ? "ol" : "ul");
    let item = null;
    for (const line of body.split("\n")) {
      const m = /^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s+(.*)$/.exec(line);
      if (m || !item) {
        item = document.createElement("li");
        list.append(item);
        inline(m ? m[1] : line, item);
      } else {
        inline(` ${line.trim()}`, item);
      }
    }
    return list;
  }
  const p = document.createElement("p");
  inline(body, p);
  return p;
}

// Fill `container` with an approximate rendering. Locked blocks are labelled boxes.
export function render(blocks, container) {
  container.replaceChildren();
  for (const block of blocks) {
    if (block.kind === "blank") continue;
    if (block.kind === "prose") {
      container.append(renderProse(block));
      continue;
    }
    const box = document.createElement("div");
    box.className = "md-locked";
    const label = document.createElement("span");
    label.className = "md-locked-label";
    label.textContent = `${block.reason}: not shown in the preview`;
    const first = document.createElement("code");
    first.textContent = editable(block).split("\n")[0].slice(0, 120);
    box.append(label, first);
    container.append(box);
  }
}
