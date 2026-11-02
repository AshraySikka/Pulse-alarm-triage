// Thin wrapper over the Django REST API. Errors carry the server's message.
// Locally the API is on the same origin (or behind the Vite proxy). When the front end is hosted
// separately (Vercel), set VITE_API_URL to the API's address, for example https://pulse-api.onrender.com.
const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

async function req(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}/api${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    const err = new Error("Can't reach the Pulse server. If this demo has been idle, the server may be waking up. Wait a minute and try again.");
    err.code = "offline";
    throw err;
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const err = new Error((data && data.error) || `The server answered ${res.status}.`);
    err.status = res.status;
    err.code = data && data.code;
    throw err;
  }
  return data;
}

const post = (path, body = {}) => req(path, { method: "POST", body });

export const api = {
  queue: () => req("/queue/"),
  incident: (id) => req(`/incidents/${id}/`),
  action: (id, action, extra = {}) => post(`/incidents/${id}/action/`, { action, ...extra }),
  judge: (id) => post(`/incidents/${id}/judge/`),
  prompt: (id) => req(`/incidents/${id}/prompt/`),
  claudeStatus: () => req("/claude/status/"),
  review: (limit = 6, force = false) => post("/claude/review/", { limit, force }),
  config: () => req("/config/"),
  saveConfig: (params) => req("/config/", { method: "PUT", body: { params, actor: "Admin" } }),
  resetConfig: () => post("/config/reset/"),
  preview: (params) => post("/config/preview/", { params }),
  alertTypes: () => req("/alert-types/"),
  updateAlertType: (code, data) => req(`/alert-types/${code}/`, { method: "PATCH", body: data }),
  patients: () => req("/patients/"),
  stats: () => req("/stats/"),
  tick: () => post("/simulate/tick/"),
  scenarios: () => req("/simulate/scenarios/"),
  scenario: (key) => post("/simulate/scenario/", { scenario: key }),
  resetDemo: () => post("/demo/reset/", { keep_config: true }),
};
