// The user guide's Markdown (#40), parsed into plain data. Nothing here touches the DOM
// or turns text into HTML; guide.js builds elements from these blocks with textContent.
//
// It understands the subset docs/user-guide/ is written in: #, ## and ### headings;
// paragraphs; "-" and "1." lists nested one level; ``` code fences; ">" blockquotes;
// pipe tables; images on their own line; links; **strong**, *emphasis* and `code`.
// Anything else is shown as plain text.

const FENCE = /^ {0,3}```\s*([\w-]*)\s*$/;
const HEADING = /^(#{1,3})\s+(.*?)\s*#*\s*$/;
const BULLET = /^( *)[-*]\s+(.*)$/;
const NUMBER = /^( *)(\d{1,3})[.)]\s+(.*)$/;
const IMAGE = /^!\[([^\]]*)\]\(([^)\s]+)\)\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const BLANK = /^\s*$/;

// GitHub's heading anchors: lower case, punctuation removed, spaces to hyphens.
export function slug(text) {
  return plainText(inline(text))
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, "")
    .trim()
    .replace(/\s/g, "-");
}

function cells(line) {
  let body = line.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|") && !body.endsWith("\\|")) body = body.slice(0, -1);
  const out = [];
  let cell = "";
  let inCode = false;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === "\\" && body[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (ch === "`") {
      inCode = !inCode;
      cell += ch;
    } else if (ch === "|" && !inCode) {
      out.push(cell.trim());
      cell = "";
    } else {
      cell += ch;
    }
  }
  out.push(cell.trim());
  return out;
}

function listItem(line) {
  const b = BULLET.exec(line);
  if (b) return { indent: b[1].length, ordered: false, text: b[2] };
  const n = NUMBER.exec(line);
  if (n) return { indent: n[1].length, ordered: true, start: Number(n[2]), text: n[3] };
  return null;
}

const indentOf = (line) => line.length - line.trimStart().length;

// The list starting at lines[i], whose items sit at `base` indent. Deeper items form a
// nested list inside the item above them.
function parseList(lines, i, base = listItem(lines[i]).indent) {
  const first = listItem(lines[i]);
  const list = { type: "list", ordered: first.ordered, start: first.start || 1, items: [] };
  let item = null;
  while (i < lines.length) {
    const line = lines[i];
    if (BLANK.test(line)) {
      let j = i + 1;
      while (j < lines.length && BLANK.test(lines[j])) j += 1;
      const next = lines[j];
      if (next === undefined || indentOf(next) < base) break;
      const nextItem = listItem(next);
      if (!nextItem && indentOf(next) <= base) break;
      if (nextItem && nextItem.indent <= base + 1 && nextItem.ordered !== list.ordered) break;
      i = j;
      continue;
    }
    const li = listItem(line);
    if (li && li.indent < base) break;
    if (li && li.indent <= base + 1) {
      if (li.ordered !== list.ordered) break;
      item = { text: li.text, children: [] };
      list.items.push(item);
      i += 1;
    } else if (li && item) {
      const nested = parseList(lines, i, li.indent);
      item.children.push(nested.block);
      i = nested.next;
    } else if (item && indentOf(line) > base) {
      item.text += " " + line.trim(); // a wrapped line of the item
      i += 1;
    } else {
      break;
    }
  }
  return { block: list, next: i };
}

export function parse(text) {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (BLANK.test(line)) {
      i += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !/^ {0,3}```\s*$/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // the closing fence
      blocks.push({ type: "code", lang: fence[1] || "", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2], id: slug(heading[2]) });
      i += 1;
      continue;
    }
    const image = IMAGE.exec(line);
    if (image) {
      blocks.push({ type: "image", alt: image[1], src: image[2] });
      i += 1;
      continue;
    }
    if (line.trimStart().startsWith(">")) {
      const body = [];
      while (i < lines.length && lines[i].trimStart().startsWith(">")) {
        body.push(lines[i].trimStart().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({ type: "quote", text: body.join(" ").trim() });
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const header = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && !BLANK.test(lines[i])) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }
    if (listItem(line)) {
      const { block, next } = parseList(lines, i);
      blocks.push(block);
      i = next;
      continue;
    }
    const body = [];
    while (
      i < lines.length &&
      !BLANK.test(lines[i]) &&
      !FENCE.test(lines[i]) &&
      !HEADING.test(lines[i]) &&
      !lines[i].trimStart().startsWith(">") &&
      !(body.length && listItem(lines[i]))
    ) {
      body.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "para", text: body.join(" ") });
  }
  return blocks;
}

// Inline text into tokens: text, strong, em, code and link (with children).
export function inline(text) {
  const out = [];
  let buf = "";
  const flush = () => {
    if (buf) out.push({ type: "text", text: buf });
    buf = "";
  };
  let i = 0;
  const s = String(text);
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && /[\\`*_[\]()#|>!-]/.test(s[i + 1])) {
      buf += s[i + 1];
      i += 2;
      continue;
    }
    if (ch === "`") {
      const end = s.indexOf("`", i + 1);
      if (end > i) {
        flush();
        out.push({ type: "code", text: s.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    if (s.startsWith("**", i)) {
      const end = s.indexOf("**", i + 2);
      if (end > i + 2) {
        flush();
        out.push({ type: "strong", children: inline(s.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    if ((ch === "*" || ch === "_") && s[i + 1] !== " " && s[i + 1] !== ch) {
      const end = s.indexOf(ch, i + 1);
      const wordStart = ch === "*" || i === 0 || !/\w/.test(s[i - 1]);
      if (end > i + 1 && wordStart && s[end - 1] !== " ") {
        flush();
        out.push({ type: "em", children: inline(s.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    }
    if (ch === "[") {
      const close = s.indexOf("](", i);
      const end = close > 0 ? s.indexOf(")", close + 2) : -1;
      if (close > i && end > close) {
        flush();
        out.push({ type: "link", href: s.slice(close + 2, end).trim(), children: inline(s.slice(i + 1, close)) });
        i = end + 1;
        continue;
      }
    }
    buf += ch;
    i += 1;
  }
  flush();
  return out;
}

export function plainText(tokens) {
  return tokens.map((t) => (t.children ? plainText(t.children) : t.text)).join("");
}
