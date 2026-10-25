import { ago, api, duration, el, every, ms, percent, pill } from "./common.js";

const id = new URLSearchParams(location.search).get("id");

async function refresh() {
  let monitor;
  try {
    monitor = await api(`/monitors/${id}`);
  } catch {
    document.getElementById("title").textContent = "Monitor not found";
    return;
  }
  const [checks, incidents] = await Promise.all([api(`/monitors/${id}/checks?limit=50`), api("/incidents?limit=500")]);
  document.title = `${monitor.name} · Pulse`;

  document.getElementById("head").replaceChildren(
    el("h1", { id: "title" }, monitor.name, " ", pill(monitor.up)),
    el("a", { class: "url", href: monitor.url, rel: "noopener", target: "_blank" }, monitor.url));

  const stat = (value, label) => el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label));
  document.getElementById("summary").replaceChildren(
    stat(percent(monitor.uptime_24h), "uptime, last 24h"),
    stat(ms(monitor.p95_latency_ms_24h), "p95 response time"),
    stat(monitor.kind, "type"),
    stat(`${monitor.interval_seconds}s`, "check interval"));

  const mine = incidents.filter((incident) => incident.monitor_id === Number(id));
  document.getElementById("no-incidents").hidden = mine.length > 0;
  document.getElementById("incidents").replaceChildren(...mine.map((incident) =>
    el("li", { class: `incident ${incident.resolved_at ? "resolved" : ""}` },
      el("strong", {}, incident.resolved_at ? `Resolved after ${duration(incident.duration_seconds)}` : "Ongoing"),
      el("span", { class: "error-text" }, incident.cause || "No details"),
      el("span", { class: "muted" }, `Opened ${ago(incident.opened_at)}`))));

  document.getElementById("no-checks").hidden = checks.length > 0;
  document.querySelector("#checks tbody").replaceChildren(...checks.map((check) =>
    el("tr", {},
      el("td", { title: new Date(check.checked_at).toLocaleString() }, ago(check.checked_at)),
      el("td", {}, pill(check.ok, ["OK", "Failed"])),
      el("td", { class: "num" }, check.status_code ?? "–"),
      el("td", { class: "num" }, ms(check.latency_ms)),
      el("td", { class: "error-text" }, check.error ?? ""),
      el("td", {}, check.has_screenshot
        ? el("a", { href: `/api/checks/${check.id}/screenshot`, target: "_blank" }, "Screenshot")
        : ""))));
}

every(10, refresh);
