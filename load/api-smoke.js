// Load test and quality gate: k6 exits non-zero when a threshold is crossed, which fails the PR.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.PULSE_URL || "http://localhost:8080";

export const options = {
  scenarios: {
    api: { executor: "constant-vus", vus: 10, duration: "30s" },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{name:list}": ["p(95)<300"],
    "http_req_duration{name:get}": ["p(95)<200"],
    checks: ["rate>0.99"],
  },
};

// A few monitors so the list and stats queries have rows to work on
export function setup() {
  const ids = [];
  for (let i = 0; i < 20; i++) {
    const body = JSON.stringify({ name: `k6-${i}`, url: "http://pulse-httpbin.pulse/status/200", interval_seconds: 60 });
    const res = http.post(`${BASE}/api/monitors`, body, { headers: { "Content-Type": "application/json" } });
    ids.push(res.json("id"));
  }
  return { ids };
}

export default function ({ ids }) {
  const list = http.get(`${BASE}/api/monitors`, { tags: { name: "list" } });
  check(list, { "list returns 200": (r) => r.status === 200 });

  const id = ids[Math.floor(Math.random() * ids.length)];
  const one = http.get(`${BASE}/api/monitors/${id}`, { tags: { name: "get" } });
  check(one, { "get returns 200": (r) => r.status === 200 });
}

export function teardown({ ids }) {
  ids.forEach((id) => http.del(`${BASE}/api/monitors/${id}`));
}
