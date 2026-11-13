// Fetch wrapper for the controller API. Every URL is relative to the page.
// State-changing requests carry the per-launch session secret (core/security.py).

const SESSION_HEADER = "x-opncafyr-session";
const meta = document.querySelector('meta[name="opncafyr-session"]');
const SESSION = meta ? meta.content : "";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.code = detail && typeof detail === "object" ? detail.code : undefined;
    this.tools = detail && typeof detail === "object" ? detail.tools : undefined;
    // The whole error detail, e.g. a conflict's current_sha256 (#52).
    this.detail = detail && typeof detail === "object" ? detail : {};
  }
}

// Thrown for any request after the app has stopped (#73): the tab shows the stopped page,
// so callers have nothing to report.
export class AppStoppedError extends Error {
  constructor() {
    super("opncafyr has stopped.");
    this.stopped = true;
  }
}

let stopped = false;
let onNetworkFailure = null;

// After this no request leaves the page.
export function markStopped() {
  stopped = true;
}

// `fn` hears every request that failed without a response (the launcher is gone or
// unreachable), as opposed to an HTTP error.
export function onConnectionFailure(fn) {
  onNetworkFailure = fn;
}

async function request(url, init) {
  if (stopped) throw new AppStoppedError();
  try {
    return await fetch(url, init);
  } catch (e) {
    if (stopped) throw new AppStoppedError();
    onNetworkFailure?.(e);
    throw e;
  }
}

// One request that bypasses the hooks above: the lifecycle uses it to confirm a lost
// connection, and the stopped page uses it for "Check again".
export async function probe(url) {
  const response = await fetch(url, { headers: { accept: "application/json" }, cache: "no-store" });
  return parse(response);
}

async function parse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const d = data.detail;
    let message = `Request failed (${response.status}).`;
    if (typeof d === "string") message = d;
    else if (d && typeof d.message === "string") message = d.message;
    throw new ApiError(message, response.status, d);
  }
  return data;
}

export async function getJSON(url) {
  const response = await request(url, { headers: { accept: "application/json" }, cache: "no-store" });
  return parse(response);
}

export async function send(method, url, body) {
  const response = await request(url, {
    method,
    headers: { "content-type": "application/json", [SESSION_HEADER]: SESSION },
    body: JSON.stringify(body || {}),
  });
  return parse(response);
}

export function post(url, body) {
  return send("POST", url, body);
}

// For page unload: the request may outlive the page, and its result is ignored.
export function postAndForget(url, body) {
  if (stopped) return;
  try {
    fetch(url, {
      method: "POST",
      keepalive: true,
      headers: { "content-type": "application/json", [SESSION_HEADER]: SESSION },
      body: JSON.stringify(body || {}),
    }).catch(() => {});
  } catch (e) {
    // Unload requests are a hint only.
  }
}
