// JSON client for the Mimikiku API. Auth rides on the httpOnly session cookie;
// a 401 anywhere means the session is gone, so the app drops back to sign-in.

import { auth } from "./auth.svelte.js";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const TIMEOUT_MS = 15000;

export async function api(path, { method = "GET", body, timeout = TIMEOUT_MS, quiet401 = false } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeout),
    });
  } catch (e) {
    throw new ApiError(0, e?.name === "TimeoutError" ? "The server took too long to answer" : "Could not reach the server");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) {
    if (res.status === 401 && !quiet401) auth.signedOut();
    throw new ApiError(res.status, detail(data) || `Request failed (${res.status})`);
  }
  // Monitoring handlers report failures as 200 {"error": "..."}
  if (data && typeof data === "object" && !Array.isArray(data) && typeof data.error === "string") {
    throw new ApiError(res.status, data.error);
  }
  return data;
}

function detail(data) {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e.msg).join("; "); // pydantic validation errors
  return "";
}

export const get = (path, opts) => api(path, opts);
export const post = (path, body, opts) => api(path, { ...opts, method: "POST", body: body ?? {} });
export const patch = (path, body, opts) => api(path, { ...opts, method: "PATCH", body });
export const del = (path, opts) => api(path, { ...opts, method: "DELETE" });
