// Shared helpers for the pages. Text that users typed (names, URLs, errors) is always added as
// text nodes, never as HTML, so a monitor named "<script>" shows up as text instead of running.

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(describe(body.detail) || `The API answered ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

// FastAPI validation errors are a list of {loc, msg}; turn them into one readable line
function describe(detail) {
  if (!Array.isArray(detail)) return detail;
  return detail.map((item) => `${item.loc.at(-1)}: ${item.msg}`).join("; ");
}

export function ago(iso) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

export function duration(seconds) {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  return `${hours} h ${Math.round((seconds % 3600) / 60)} min`;
}

export function pill(up, labels = ["Up", "Down", "Pending"]) {
  const [tone, text] = up === true ? ["up", labels[0]] : up === false ? ["down", labels[1]] : ["pending", labels[2]];
  return el("span", { class: `pill ${tone}` }, text);
}

export const percent = (value) => (value === null ? "–" : `${Number(value).toFixed(2)}%`);
export const ms = (value) => (value === null ? "–" : `${Math.round(value)} ms`);

// Run now, then every few seconds; a failed refresh shows a banner instead of breaking the page
export function every(seconds, refresh) {
  const banner = document.getElementById("offline");
  const run = async () => {
    try {
      await refresh();
      banner.hidden = true;
    } catch (error) {
      banner.textContent = `Could not load the data: ${error.message}. Retrying every ${seconds}s.`;
      banner.hidden = false;
    }
  };
  run();
  setInterval(run, seconds * 1000);
}
