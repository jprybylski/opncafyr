// The user guide inside the app (#40): "#/guide" and "#/guide/<page>[/<heading>]".
// Pages are the Markdown files of docs/user-guide/, shipped with the app and served
// under static/guide/. They are parsed by guidemd.js and built here with createElement
// and textContent only, so no guide text is ever treated as HTML. Links go to other
// guide pages, or download the guide's practice data (example/*.csv); the app works offline.

import { $, el, on } from "./dom.js";
import { inline, parse, plainText } from "./guidemd.js";

const PAGE = /^[a-z0-9-]+$/;
const PAGE_LINK = /^([a-z0-9-]+)\.md(?:#([\w-]+))?$/;
const IMAGE_SRC = /^images\/[a-z0-9-]+\.png$/;
const EXAMPLE_FILE = /^example\/[\w.-]+\.csv$/; // practice data shipped with the guide
let shown = null;
let chapters = null;

function href(target, page) {
  const local = /^#([\w-]+)$/.exec(target);
  if (local) return `#/guide/${page}/${local[1]}`;
  const other = PAGE_LINK.exec(target);
  if (other) return `#/guide/${other[1]}${other[2] ? `/${other[2]}` : ""}`;
  return null;
}

function inlineNodes(tokens, page) {
  return tokens.map((t) => {
    if (t.type === "text") return document.createTextNode(t.text);
    if (t.type === "code") return el("code", t.text);
    if (t.type === "strong" || t.type === "em") {
      const node = el(t.type);
      node.append(...inlineNodes(t.children, page));
      return node;
    }
    const a = el("a");
    if (EXAMPLE_FILE.test(t.href)) {
      a.href = `static/guide/${t.href}`;
      a.setAttribute("download", t.href.slice("example/".length));
      a.append(...inlineNodes(t.children, page));
      return a;
    }
    const target = href(t.href, page);
    if (!target) return document.createTextNode(plainText(t.children)); // no links out
    a.href = target;
    a.append(...inlineNodes(t.children, page));
    return a;
  });
}

const text = (tag, md, page, className) => {
  const node = el(tag, null, className);
  node.append(...inlineNodes(inline(md), page));
  return node;
};

function listNode(block, page) {
  const list = el(block.ordered ? "ol" : "ul");
  if (block.ordered && block.start !== 1) list.start = block.start;
  for (const item of block.items) {
    const li = text("li", item.text, page);
    for (const child of item.children) li.append(listNode(child, page));
    list.append(li);
  }
  return list;
}

function blockNode(block, page) {
  switch (block.type) {
    case "heading": {
      const node = text(`h${Math.max(block.level, 2)}`, block.text, page); // the title is the h1
      node.id = `guide-${block.id}`;
      node.tabIndex = -1;
      return node;
    }
    case "para":
      return text("p", block.text, page);
    case "quote":
      return text("blockquote", block.text, page, "guide-note");
    case "list":
      return listNode(block, page);
    case "code": {
      const pre = el("pre");
      pre.tabIndex = 0; // it may scroll
      pre.append(el("code", block.text));
      return pre;
    }
    case "table": {
      const wrap = el("div", null, "table-wrap");
      const table = el("table", null, "table");
      const head = el("tr");
      head.append(...block.header.map((c) => text("th", c, page)));
      const thead = el("thead");
      thead.append(head);
      const tbody = el("tbody");
      for (const row of block.rows) {
        const tr = el("tr");
        tr.append(...row.map((c) => text("td", c, page)));
        tbody.append(tr);
      }
      table.append(thead, tbody);
      wrap.append(table);
      return wrap;
    }
    case "image": {
      if (!IMAGE_SRC.test(block.src)) return document.createTextNode("");
      const figure = el("figure", null, "guide-figure");
      const img = el("img");
      img.src = `static/guide/${block.src}`;
      img.alt = block.alt;
      img.loading = "lazy";
      figure.append(img);
      return figure;
    }
    default:
      return document.createTextNode("");
  }
}

async function fetchPage(page) {
  const response = await fetch(`static/guide/${page}.md`, { cache: "no-cache" });
  if (!response.ok) throw new Error(response.status === 404 ? "missing" : `HTTP ${response.status}`);
  return response.text();
}

// The chapter list: the links in index.md, in order.
async function loadChapters() {
  if (chapters) return chapters;
  const found = [{ page: "index", title: "Contents" }];
  try {
    const walk = (tokens) => {
      for (const t of tokens) {
        const m = t.type === "link" ? PAGE_LINK.exec(t.href) : null;
        if (m && !m[2] && !found.some((c) => c.page === m[1])) found.push({ page: m[1], title: plainText(t.children) });
        if (t.children) walk(t.children);
      }
    };
    const visit = (block) => {
      if (block.type === "list") {
        for (const item of block.items) {
          walk(inline(item.text));
          item.children.forEach(visit);
        }
      }
    };
    parse(await fetchPage("index")).forEach(visit);
  } catch (e) {
    // The page itself says the guide is missing.
  }
  chapters = found;
  return chapters;
}

function renderNav(list, page) {
  const nav = $("guide-chapters");
  nav.replaceChildren(
    ...list.map((c) => {
      const li = el("li");
      const a = el("a", c.title);
      a.href = c.page === "index" ? "#/guide" : `#/guide/${c.page}`;
      if (c.page === page) a.setAttribute("aria-current", "page");
      li.append(a);
      return li;
    }),
  );
}

async function show(page, anchor) {
  const article = $("guide-article");
  const heading = $("guide-heading");
  if (!PAGE.test(page)) page = "index";
  const key = `${page}#${anchor || ""}`;
  if (shown === key) return;
  shown = key;
  renderNav(await loadChapters(), page);
  let blocks;
  try {
    blocks = parse(await fetchPage(page));
  } catch (e) {
    heading.textContent = "Guide";
    article.replaceChildren(
      el("p", e.message === "missing" ? "This page of the guide isn't installed." : "The guide couldn't be loaded."),
    );
    return;
  }
  const title = blocks.find((b) => b.type === "heading" && b.level === 1);
  heading.textContent = title ? plainText(inline(title.text)) : "Guide";
  document.title = `${heading.textContent} · opncafyr`;
  article.replaceChildren(...blocks.filter((b) => b !== title).map((b) => blockNode(b, page)));
  if (anchor) {
    const target = document.getElementById(`guide-${anchor}`);
    if (target) target.focus({ preventScroll: false });
  } else {
    article.scrollTop = 0;
  }
}

export function initGuide() {
  on("view-changed", (route) => {
    if (route.name === "guide") show(route.param || "index", route.file);
    else shown = null;
  });
}
