import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { LedgerPanel, RedteamPanel, TimelinePanel } from "./components/Audit";
import { DiagnosisPanel } from "./components/Diagnosis";
import { EvidencePanel, RoutingPanel } from "./components/Evidence";
import { PipelinePanel } from "./components/Pipeline";
import { PlanPanel } from "./components/Plan";
import { HeroPanel, TrafficPanel } from "./components/Traffic";
import { SourceBadge, TooltipLayer, fmt } from "./components/common";
import type { Incident, IncidentSummary, Meta, RedteamCatalog, Refusal } from "./types";

const TRANSIENT = new Set(["DIAGNOSING", "VERIFYING", "EXECUTING"]);

function Logo() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <svg width="20" height="20" viewBox="0 0 32 32">
        <path d="M18 3 7 18h7l-2 11 12-16h-7z" fill="var(--accent)" />
      </svg>
    </div>
  );
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [refusals, setRefusals] = useState<Refusal[]>([]);
  const [catalog, setCatalog] = useState<RedteamCatalog>({});
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [theme, setTheme] = useState<"auto" | "dark" | "light">("auto");
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const refreshLists = useCallback(async () => {
    const [list, refused] = await Promise.all([api.incidents(), api.refusals()]);
    setIncidents(list);
    setRefusals(refused);
    return list;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [m, c] = await Promise.all([api.meta(), api.redteamCatalog()]);
        setMeta(m);
        setCatalog(c);
        const list = await refreshLists();
        if (list.length) setSelectedId(list[0].incident_id);
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : String(error));
      }
    })();
  }, [refreshLists]);

  const stage = incident?.stage ?? "";
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api.incident(selectedId);
        if (!cancelled) setIncident(next);
      } catch {
        /* keep the previous render; no skeleton flash */
      }
    };
    void tick();
    const timer = window.setInterval(tick, TRANSIENT.has(stage) ? 1200 : 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId, stage]);

  const act = async (label: string, fn: () => Promise<Incident | unknown>, success?: string) => {
    setBusy(true);
    try {
      const result = await fn();
      if (result && typeof result === "object" && "incident_id" in (result as Incident)) {
        const next = result as Incident;
        setIncident(next);
        setSelectedId(next.incident_id);
      }
      await refreshLists();
      if (success) setToast({ ok: true, text: success });
    } catch (error) {
      const text =
        error instanceof ApiError ? `${label} refused — ${error.code}: ${error.message}` : `${label} failed: ${String(error)}`;
      setToast({ ok: false, text });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 9000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const focusEvidence = (id: string) => {
    setFocused(id);
    document.getElementById(`evidence-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const readOnly = !meta || meta.read_only || meta.mode === "replay";
  const source = meta?.source ?? "OFFLINE_SIMULATION";

  return (
    <TooltipLayer>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <Logo />
            <div>
              <div className="brand-name">TrafficFuse AI</div>
              <div className="brand-tag">The AI SRE that proves it fixed the incident</div>
            </div>
          </div>
          <div className="stack" style={{ alignItems: "flex-end", gap: 6 }}>
            <div className="row">
              {meta?.source ? <SourceBadge source={meta.source} title={meta.source_description} /> : null}
              {meta?.synthetic_workload ? <span className="badge">Synthetic lab workload</span> : null}
              <button
                className="btn ghost"
                style={{ padding: "3px 10px" }}
                onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "auto" : "dark")}
                aria-label="Toggle color theme"
              >
                Theme: {theme}
              </button>
            </div>
            {meta ? (
              <div className="identity">
                {meta.cluster ? (
                  <span>
                    <b>{meta.cluster.cluster}</b> · {meta.cluster.context} · ns {meta.cluster.namespace}
                  </span>
                ) : null}
                {meta.aig ? (
                  <span>
                    <b>
                      AIG {meta.aig.version}
                    </b>{" "}
                    @{(meta.aig.commit ?? "").slice(0, 7)} · {meta.aig.execution_budget_seconds}s budget
                  </span>
                ) : null}
                {meta.model ? (
                  <span>
                    <b>{meta.model.name}</b> {meta.model.available ? "local model ready" : "model unavailable"}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </header>

        {loadError ? <div className="banner error">Backend unavailable: {loadError}</div> : null}
        {meta?.mode === "simulation" ? (
          <div className="banner sim">
            <span>
              <b>Offline simulation.</b> In-process lab services behind a simulated Service — no Kubernetes is involved. The
              governance runtime, probe evidence, hashes and receipts are real; cluster routing is simulated. Live-Kind results
              are shown only in <b>live</b> mode.
            </span>
          </div>
        ) : null}
        {meta?.mode === "replay" ? (
          <div className="banner replay">
            <span>
              <b>Recorded replay — read-only.</b> Evidence recorded from the live Kind lab is replayed unchanged; nothing is
              re-measured and no governed operation is available here.
            </span>
          </div>
        ) : null}

        <div className="spread" style={{ marginBottom: 16 }}>
          <div className="row">
            <label className="small muted" htmlFor="incident-select">
              Incident
            </label>
            <select
              id="incident-select"
              className="input"
              value={selectedId ?? ""}
              onChange={(e) => setSelectedId(e.target.value || null)}
            >
              {incidents.length === 0 ? <option value="">No incidents yet</option> : null}
              {incidents.map((i) => (
                <option key={i.incident_id} value={i.incident_id}>
                  {i.incident_id} · {i.stage} · {fmt.pct(i.live_error_percent)} errors
                </option>
              ))}
            </select>
            {incident ? <span className="chip">stage {incident.stage}</span> : null}
          </div>
          {!readOnly ? (
            <div className="row">
              {meta?.mode === "simulation" ? (
                <button className="btn ghost" disabled={busy} onClick={() => act("Lab reset", api.resetLab, "Simulated lab reset to version=faulty")}>
                  Reset simulated lab
                </button>
              ) : null}
              <button className="btn primary" disabled={busy} onClick={() => act("Detection", api.detect)}>
                {busy ? <span className="spinner" aria-hidden="true" /> : null} Probe live Service now
              </button>
            </div>
          ) : null}
        </div>

        {!incident ? (
          <section className="panel" style={{ textAlign: "center", padding: 48 }}>
            <h2 style={{ marginTop: 0 }}>No incident selected</h2>
            <p className="secondary">
              TrafficFuse probes the customer-facing Service and the healthy baseline with real HTTP requests, then opens an
              incident when the measured error rate crosses the policy threshold.
            </p>
            {!readOnly ? (
              <button className="btn primary" disabled={busy} onClick={() => act("Detection", api.detect)}>
                Probe live Service now
              </button>
            ) : null}
          </section>
        ) : (
          <div className="grid">
            <div className="span-5">
              <HeroPanel incident={incident} />
            </div>
            <div className="span-7">
              <PipelinePanel incident={incident} />
            </div>
            <div className="span-7">
              <TrafficPanel incident={incident} />
            </div>
            <div className="span-5">
              <DiagnosisPanel
                incident={incident}
                onFocusEvidence={focusEvidence}
                canAct={!readOnly}
                busy={busy}
                onDiagnose={() => act("Diagnosis", () => api.diagnose(incident.incident_id))}
              />
            </div>
            <div className="span-7">
              {meta ? (
                <PlanPanel
                  incident={incident}
                  meta={meta}
                  token={token}
                  setToken={setToken}
                  busy={busy}
                  onApprove={(approver, reason) =>
                    act(
                      "Approval",
                      () => api.approve(incident.incident_id, incident.run?.plan_fingerprint ?? "", approver, reason),
                      "Exact Plan approved — nothing dispatched",
                    )
                  }
                  onReject={(approver, reason) => act("Rejection", () => api.reject(incident.incident_id, approver, reason))}
                  onAuthorize={() =>
                    act("Authorization", () => api.authorize(incident.incident_id, token), "One Action authorized — still nothing dispatched")
                  }
                  onExecute={() => act("Execution", () => api.execute(incident.incident_id, token))}
                  onVerify={() => act("Verification", () => api.verify(incident.incident_id))}
                />
              ) : null}
            </div>
            <div className="span-5">
              <EvidencePanel incident={incident} focused={focused} />
            </div>
            <div className="span-5">
              <RoutingPanel incident={incident} />
            </div>
            <div className="span-7">
              <LedgerPanel incident={incident} />
            </div>
            <div className="span-7">
              <TimelinePanel events={incident.events} />
            </div>
            <div className="span-5">
              <RedteamPanel
                catalog={catalog}
                refusals={refusals}
                canAct={!readOnly}
                busy={busy}
                onRun={(scenario) => act("Red-team proposal", () => api.redteam(scenario), "Unsafe proposal submitted to the gate")}
              />
            </div>
          </div>
        )}

        <footer className="small muted" style={{ marginTop: 24 }}>
          Source label on every panel: {source}. Governance by the pinned, pre-existing MIT-licensed Agentic Intent Gate v0.2.0
          runtime (reused as an installed dependency). TrafficFuse never claims recovery from an API success — only from
          re-measured traffic.
        </footer>
        {toast ? (
          <div className={`toast ${toast.ok ? "ok" : ""}`} role="status">
            {toast.text}
          </div>
        ) : null}
      </div>
    </TooltipLayer>
  );
}
