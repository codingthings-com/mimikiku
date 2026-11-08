// Polls the monitoring API. One fetch cycle reads all four endpoints in parallel so the
// dashboard always shows a consistent snapshot, and a failing endpoint only blanks its own panel.

const ENDPOINTS = {
  status: "/api/models/status",
  metrics: "/api/models/metrics",
  pipelines: "/api/pipelines/status",
  sessions: "/api/websocket/sessions",
};

export const INTERVALS = [5, 10, 15, 30, 60];
const TIMEOUT_MS = 8000;

async function getJson(url, options = {}) {
  const res = await fetch(url, { ...options, signal: AbortSignal.timeout(options.timeout ?? TIMEOUT_MS) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  // The server reports handler failures as 200 {"error": "..."}
  if (data?.error) throw new Error(data.error);
  return data;
}

function remembered(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v ?? fallback;
  } catch {
    return fallback;
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

class Monitor {
  data = $state({ status: null, metrics: null, pipelines: null, sessions: null });
  errors = $state({ status: null, metrics: null, pipelines: null, sessions: null });
  loading = $state(false);
  loaded = $state(false); // true once the first fetch cycle has finished
  updatedAt = $state(0);
  auto = $state(remembered("monitor.auto", true));
  interval = $state(remembered("monitor.interval", 15));

  #timer = null;
  #inflight = null;

  /** `manual` refreshes show the busy state; background polls update silently. */
  async refresh(manual = false) {
    // A tick that lands while a fetch is still running joins it instead of piling up.
    if (this.#inflight) return this.#inflight;
    this.loading = manual;
    this.#inflight = (async () => {
      const keys = Object.keys(ENDPOINTS);
      const results = await Promise.allSettled(keys.map((k) => getJson(ENDPOINTS[k])));
      results.forEach((r, i) => {
        const key = keys[i];
        if (r.status === "fulfilled") {
          this.data[key] = r.value;
          this.errors[key] = null;
        } else {
          this.errors[key] = r.reason?.message ?? String(r.reason);
        }
      });
      if (results.some((r) => r.status === "fulfilled")) this.updatedAt = Date.now();
      this.loading = false;
      this.loaded = true;
      this.#inflight = null;
    })();
    return this.#inflight;
  }

  /** Run the polling timer according to `auto` / `interval`. Call again after changing either. */
  schedule() {
    clearInterval(this.#timer);
    this.#timer = null;
    if (this.auto) this.#timer = setInterval(() => this.refresh(), this.interval * 1000);
  }

  setAuto(on) {
    this.auto = on;
    remember("monitor.auto", on);
    this.schedule();
    if (on) this.refresh();
  }

  setInterval(seconds) {
    this.interval = seconds;
    remember("monitor.interval", seconds);
    this.schedule();
  }

  start() {
    this.refresh();
    this.schedule();
  }

  stop() {
    clearInterval(this.#timer);
    this.#timer = null;
  }

  cleanup() {
    return getJson("/api/models/cleanup", { method: "POST", timeout: 30000 });
  }
}

export const monitor = new Monitor();
