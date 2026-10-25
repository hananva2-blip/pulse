import { ago, api, duration, el, every, ms, percent, pill } from "./common.js";

const tbody = document.querySelector("#monitors tbody");

async function refresh() {
  const [monitors, incidents] = await Promise.all([api("/monitors"), api("/incidents?status=open")]);
  renderSummary(monitors, incidents);
  renderIncidents(incidents);
  tbody.replaceChildren(...monitors.map(row));
  document.getElementById("no-monitors").hidden = monitors.length > 0;
  document.getElementById("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function renderSummary(monitors, incidents) {
  const count = (up) => monitors.filter((monitor) => monitor.up === up).length;
  const stat = (value, label, tone) => el("div", { class: `stat ${tone}` }, el("b", {}, String(value)), el("span", {}, label));
  document.getElementById("summary").replaceChildren(
    stat(count(true), "up", "up"),
    stat(count(false), "down", count(false) ? "down" : ""),
    stat(incidents.length, "open incidents", incidents.length ? "down" : ""),
  );
}

function renderIncidents(incidents) {
  document.getElementById("incidents-section").hidden = incidents.length === 0;
  document.getElementById("incidents").replaceChildren(...incidents.map((incident) =>
    el("li", { class: "incident" },
      el("strong", {}, el("a", { href: `/monitor?id=${incident.monitor_id}` }, incident.monitor_name)),
      el("span", { class: "error-text" }, incident.cause || "No details"),
      el("span", { class: "muted" }, `Down for ${duration(incident.duration_seconds)}`))));
}

function row(monitor) {
  return el("tr", { "data-monitor": monitor.name },
    el("td", {}, pill(monitor.up)),
    el("td", {},
      el("a", { href: `/monitor?id=${monitor.id}` }, monitor.name),
      el("div", { class: "muted url" }, monitor.url)),
    el("td", {}, el("span", { class: "tag" }, monitor.kind)),
    el("td", { class: "num" }, percent(monitor.uptime_24h)),
    el("td", { class: "num" }, ms(monitor.p95_latency_ms_24h)),
    el("td", {}, monitor.last_checked_at ? ago(monitor.last_checked_at) : "Not checked yet"),
    el("td", {}, deleteButton(monitor)));
}

// Two clicks to delete: the first one asks, the second one deletes
function deleteButton(monitor) {
  const button = el("button", { class: "quiet", type: "button", "aria-label": `Delete ${monitor.name}` }, "Delete");
  button.addEventListener("click", async () => {
    if (button.dataset.armed !== "yes") {
      button.dataset.armed = "yes";
      button.textContent = "Confirm delete";
      setTimeout(() => { button.dataset.armed = ""; button.textContent = "Delete"; }, 4000);
      return;
    }
    button.disabled = true;
    await api(`/monitors/${monitor.id}`, { method: "DELETE" });
    await refresh();
  });
  return button;
}

// A browser monitor asks for text to find; an HTTP monitor asks for the expected status
const form = document.getElementById("add");
const kind = document.getElementById("kind");
kind.addEventListener("change", () => {
  document.getElementById("text-field").hidden = kind.value !== "browser";
  document.getElementById("status-field").hidden = kind.value === "browser";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const body = {
    name: data.get("name"),
    url: data.get("url"),
    kind: data.get("kind"),
    interval_seconds: Number(data.get("interval_seconds")),
  };
  if (body.kind === "browser") {
    const text = data.get("text").trim();
    if (text) body.steps = [{ action: "expect_text", value: text }];
  } else {
    body.expected_status = Number(data.get("expected_status"));
  }

  const error = document.getElementById("form-error");
  try {
    await api("/monitors", { method: "POST", body: JSON.stringify(body) });
    form.reset();
    kind.dispatchEvent(new Event("change"));
    error.hidden = true;
    await refresh();
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  }
});

every(10, refresh);
