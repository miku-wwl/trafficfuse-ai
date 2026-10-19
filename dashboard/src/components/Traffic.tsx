import type { Incident, ProbeView } from "../types";
import { Panel, SourceBadge, Status, fmt, useTip } from "./common";

const SCENARIO_LABEL: Record<string, string> = {
  live: "Before · live Service",
  baseline: "Healthy-version baseline",
  verify: "After · live Service",
};

function versionClass(version: string) {
  if (version === "healthy") return "seg-healthy";
  if (version === "faulty") return "seg-faulty";
  return "seg-other";
}

function RequestStrip({ probe }: { probe: ProbeView }) {
  const tip = useTip();
  if (probe.integrity !== "verified") {
    return (
      <Status tone="critical">
        Per-request records withheld: stored evidence {probe.integrity === "mismatch" ? "fails its SHA-256 check" : "is unavailable"}
      </Status>
    );
  }
  return (
    <div className="strip" role="list" aria-label={`${probe.records.length} requests to ${probe.target_service}`}>
      {probe.records.map((record) => {
        const failed = record.outcome !== "success";
        const content = (
          <div>
            <div className="strong">Request #{record.seq}</div>
            <div>
              {record.status === null ? "No HTTP response" : `HTTP ${record.status}`} · {record.outcome}
            </div>
            <div>{fmt.ms(record.ms)}</div>
            <div className="muted">
              served by {record.version ?? "unreported"}
              {record.pod ? ` · ${record.pod}` : ""}
            </div>
          </div>
        );
        return (
          <div
            key={record.seq}
            role="listitem"
            tabIndex={0}
            className={`cell ${failed ? (record.status === null ? "none" : "err") : ""}`}
            aria-label={`Request ${record.seq}: ${record.status ?? "no response"}, ${record.outcome}, ${Math.round(record.ms)} ms, ${record.version ?? "unreported"}`}
            onMouseMove={(event) => tip.show(event, content)}
            onFocus={(event) => tip.show(event, content)}
            onMouseLeave={tip.hide}
            onBlur={tip.hide}
          />
        );
      })}
    </div>
  );
}

function StackedBar({ parts }: { parts: { key: string; value: number; className: string; label: string }[] }) {
  const tip = useTip();
  const total = parts.reduce((sum, part) => sum + part.value, 0) || 1;
  return (
    <div className="bar" role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}>
      {parts
        .filter((part) => part.value > 0)
        .map((part) => (
          <span
            key={part.key}
            className={part.className}
            style={{ width: `${(100 * part.value) / total}%` }}
            onMouseMove={(event) =>
              tip.show(
                event,
                <span>
                  {part.label}: <b>{part.value}</b> of {total}
                </span>,
              )
            }
            onMouseLeave={tip.hide}
          />
        ))}
    </div>
  );
}

function ProbeRow({ probe }: { probe: ProbeView }) {
  const ok = probe.success_count;
  const errors = probe.error_count;
  const versions = Object.entries(probe.served_versions);
  const bad = probe.observed_error_percent >= 5;
  return (
    <div className="probe-row">
      <div>
        <div className="probe-name">{SCENARIO_LABEL[probe.scenario] ?? probe.scenario}</div>
        <div className="probe-rate" style={{ color: bad ? "var(--critical-text)" : "var(--good-text)" }}>
          {fmt.pct(probe.observed_error_percent)}
          <span className="small muted" style={{ fontWeight: 500 }}>
            {" "}errors
          </span>
        </div>
        <div className="probe-meta">
          {errors}/{probe.total_requests} failed · p50 {fmt.ms(probe.latency_ms.p50)} · p95 {fmt.ms(probe.latency_ms.p95)}
        </div>
        <div className="probe-meta mono" title={`evidence sha256 ${probe.evidence_sha256}`}>
          {probe.target_service} · {fmt.time(probe.completed_at_utc)} · sha256 {fmt.hash(probe.evidence_sha256, 10)}
        </div>
      </div>
      <div className="stack" style={{ gap: 6 }}>
        <RequestStrip probe={probe} />
        <StackedBar
          parts={[
            { key: "ok", value: ok, className: "seg-ok", label: "Succeeded (2xx)" },
            { key: "err", value: errors, className: "seg-err", label: "Failed (5xx / no response)" },
          ]}
        />
        <div className="legend">
          <span>
            <i className="seg-ok" />
            {ok} succeeded
          </span>
          <span>
            <i className="seg-err" />
            {errors} failed
          </span>
          <span className="muted">
            {Object.entries(probe.status_distribution)
              .map(([code, count]) => `${code === "no_http_response" ? "no response" : `HTTP ${code}`}: ${count}`)
              .join(" · ")}
          </span>
        </div>
        <StackedBar
          parts={versions.map(([version, count]) => ({
            key: version,
            value: count,
            className: versionClass(version),
            label: `Served by ${version}`,
          }))}
        />
        <div className="legend">
          {versions.map(([version, count]) => (
            <span key={version}>
              <i className={versionClass(version)} />
              served by {version}: {count}
            </span>
          ))}
        </div>
        <details>
          <summary className="small muted" style={{ cursor: "pointer" }}>
            Request table ({probe.records.length} rows, {probe.integrity === "verified" ? "hash verified" : probe.integrity})
          </summary>
          <div style={{ maxHeight: 220, overflow: "auto", marginTop: 6 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Status</th>
                  <th>Outcome</th>
                  <th className="num">Latency</th>
                  <th>Version</th>
                  <th>Pod</th>
                </tr>
              </thead>
              <tbody>
                {probe.records.map((r) => (
                  <tr key={r.seq}>
                    <td className="num">{r.seq}</td>
                    <td>{r.status ?? "—"}</td>
                    <td>{r.outcome}</td>
                    <td className="num">{r.ms.toFixed(1)}</td>
                    <td>{r.version ?? "—"}</td>
                    <td className="mono">{r.pod ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </div>
  );
}

export function TrafficPanel({ incident }: { incident: Incident }) {
  const order = ["live", "baseline", "verify"];
  const probes = [...incident.probes].sort((a, b) => order.indexOf(a.scenario) - order.indexOf(b.scenario));
  return (
    <Panel
      title="Traffic health"
      sub="Every value below is computed from real HTTP responses; the lab workload itself is synthetic."
      right={<SourceBadge source={incident.source} />}
    >
      {probes.map((probe) => (
        <ProbeRow key={probe.probe_id} probe={probe} />
      ))}
      {!incident.verification ? (
        <div className="small muted" style={{ paddingTop: 8 }}>
          The “after” probe appears once TrafficFuse has independently re-measured the live Service.
        </div>
      ) : null}
    </Panel>
  );
}

export function HeroPanel({ incident }: { incident: Incident }) {
  const before = incident.probes.find((p) => p.scenario === "live");
  const after = incident.verification?.after;
  const timing = incident.timing.seconds_from_detection;
  const verdict = incident.verification?.verdict;
  const p95Before = before?.latency_ms.p95 ?? null;
  const p95After = after?.latency_ms.p95 ?? null;
  return (
    <Panel
      title="Incident overview"
      sub={
        <span className="mono">
          {incident.incident_id} · detected {fmt.dateTime(incident.created_at)}
        </span>
      }
      right={
        verdict ? (
          <Status tone={verdict === "RECOVERED" ? "good" : verdict === "NOT_RECOVERED" ? "critical" : "warning"}>
            {verdict === "RECOVERED" ? "Recovery proven by measurement" : verdict}
          </Status>
        ) : incident.assessment.degraded ? (
          <Status tone="critical">Live Service degraded</Status>
        ) : (
          <Status tone="good">No degradation measured</Status>
        )
      }
    >
      <div className="hero">
        <div>
          <div className="hero-figure" style={{ color: "var(--critical-text)" }}>
            {fmt.pct(before?.observed_error_percent)}
          </div>
          <div className="hero-label">live error rate at detection</div>
        </div>
        {after ? (
          <>
            <div className="hero-arrow" aria-hidden="true">
              →
            </div>
            <div>
              <div className="hero-figure" style={{ color: after.observed_error_percent < 5 ? "var(--good-text)" : "var(--critical-text)" }}>
                {fmt.pct(after.observed_error_percent)}
              </div>
              <div className="hero-label">same Service, independently re-measured</div>
            </div>
          </>
        ) : null}
      </div>
      <div className="kpis">
        <div className="tile">
          <div className="tile-label">Failed requests</div>
          <div className="tile-value">
            {before ? `${before.error_count}/${before.total_requests}` : "—"}
            {after ? ` → ${after.error_count}/${after.total_requests}` : ""}
          </div>
          <div className="tile-delta">real HTTP GETs per probe</div>
        </div>
        <div className="tile">
          <div className="tile-label">Live p95 latency</div>
          <div className="tile-value">
            {fmt.ms(p95Before)}
            {after ? ` → ${fmt.ms(p95After)}` : ""}
          </div>
          <div className="tile-delta">
            {after && p95Before !== null && p95After !== null
              ? `${p95After - p95Before > 0 ? "+" : ""}${Math.round(p95After - p95Before)} ms vs before`
              : "measured, not modeled"}
          </div>
        </div>
        <div className="tile">
          <div className="tile-label">Served by version</div>
          <div className="tile-value">
            {before ? Object.keys(before.served_versions).join(", ") : "—"}
            {after ? ` → ${Object.keys(after.served_versions).join(", ")}` : ""}
          </div>
          <div className="tile-delta">self-reported by each responding Pod</div>
        </div>
        <div className="tile">
          <div className="tile-label">Approval → proven recovery</div>
          <div className="tile-value">
            {timing.verified_at !== undefined && timing.approved_at !== undefined
              ? fmt.duration(timing.verified_at - timing.approved_at)
              : "—"}
          </div>
          <div className="tile-delta">
            detection → recovery {fmt.duration(timing.verified_at)} (wall clock, incl. human review)
          </div>
        </div>
      </div>
    </Panel>
  );
}
