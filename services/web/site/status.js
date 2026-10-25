import { api, duration, el, every, percent, pill } from "./common.js";

// The public view: a service counts as down only while it has an open incident, so a single
// failed check (below the monitor's failures_to_alert) does not turn the page red
async function refresh() {
  const [monitors, open, incidents] = await Promise.all(
    [api("/monitors"), api("/incidents?status=open&limit=500"), api("/incidents?limit=20")]);
  const openIds = new Set(open.map((incident) => incident.monitor_id));

  const overall = document.getElementById("overall");
  const down = monitors.filter((monitor) => openIds.has(monitor.id)).length;
  overall.textContent = down ? `${down} of ${monitors.length} services are down` : "All systems operational";
  overall.classList.remove("loading");
  overall.classList.toggle("down", down > 0);

  document.getElementById("services").replaceChildren(...monitors.map((monitor) =>
    el("li", { "data-service": monitor.name },
      el("span", {}, monitor.name),
      el("span", { class: "right" },
        el("span", { class: "muted" }, `${percent(monitor.uptime_24h)} uptime`),
        pill(!openIds.has(monitor.id), ["Operational", "Down"])))));

  document.getElementById("no-history").hidden = incidents.length > 0;
  document.getElementById("history").replaceChildren(...incidents.map((incident) =>
    el("li", { class: `incident ${incident.resolved_at ? "resolved" : ""}` },
      el("strong", {}, incident.monitor_name),
      el("span", {}, incident.resolved_at
        ? `Resolved after ${duration(incident.duration_seconds)}`
        : `Ongoing for ${duration(incident.duration_seconds)}`),
      el("span", { class: "muted" }, new Date(incident.opened_at).toLocaleString()))));
}

every(15, refresh);
