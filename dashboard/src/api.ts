import type { Incident, IncidentSummary, Meta, RedteamCatalog, Refusal } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown, token?: string): Promise<T> {
  // Custom header: mutating routes require it (forces a CORS preflight that the
  // server never grants to other origins, so other sites cannot forge requests).
  const headers: Record<string, string> = { "X-TrafficFuse-Client": "dashboard" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // The execution capability travels only in this one request header; it is
  // never stored by the dashboard beyond in-memory component state.
  if (token) headers["Authorization"] = `Bearer ${token.trim()}`;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload?.detail;
    const code = typeof detail === "object" && detail ? detail.code ?? "ERROR" : "ERROR";
    const message =
      typeof detail === "object" && detail ? detail.message ?? JSON.stringify(detail) : String(detail ?? response.statusText);
    throw new ApiError(response.status, code, message);
  }
  return payload as T;
}

export const api = {
  meta: () => call<Meta>("GET", "/api/meta"),
  incidents: () => call<IncidentSummary[]>("GET", "/api/incidents"),
  incident: (id: string) => call<Incident>("GET", `/api/incidents/${id}`),
  detect: () => call<Incident>("POST", "/api/incidents"),
  diagnose: (id: string) => call<Incident>("POST", `/api/incidents/${id}/diagnose`),
  approve: (id: string, fingerprint: string, approver: string, reason: string) =>
    call<Incident>("POST", `/api/incidents/${id}/approve`, {
      plan_fingerprint: fingerprint,
      approver,
      reason: reason || null,
    }),
  reject: (id: string, approver: string, reason: string) =>
    call<Incident>("POST", `/api/incidents/${id}/reject`, { approver, reason: reason || null }),
  authorize: (id: string, token: string) => call<Incident>("POST", `/api/incidents/${id}/authorize`, undefined, token),
  execute: (id: string, token: string) => call<Incident>("POST", `/api/incidents/${id}/execute`, undefined, token),
  verify: (id: string) => call<Incident>("POST", `/api/incidents/${id}/verify`),
  refusals: () => call<Refusal[]>("GET", "/api/refusals"),
  redteamCatalog: () => call<RedteamCatalog>("GET", "/api/redteam"),
  redteam: (scenario: string) => call<Refusal>("POST", `/api/redteam/${scenario}`),
  resetLab: () => call<{ status: string }>("POST", "/api/lab/reset"),
};
