import type { EventRow, Incident, RedteamCatalog, Refusal } from "../types";
import { Panel, Status, fmt } from "./common";

const OUTCOME_TONE: Record<string, "good" | "critical" | "warning" | "info" | "pending"> = {
  degraded: "critical",
  healthy: "good",
  grounded: "good",
  validated: "good",
  approved: "good",
  admitted: "info",
  authorized: "good",
  verified: "good",
  succeeded: "good",
  recovered: "good",
  started: "pending",
  rejected: "critical",
  refused: "critical",
  failed: "warning",
  unknown: "critical",
  unavailable: "warning",
  error: "critical",
  not_recovered: "critical",
  inconclusive: "warning",
};

export function TimelinePanel({ events }: { events: EventRow[] }) {
  return (
    <Panel title="Decision & action timeline" sub="Append-only TrafficFuse audit log; governance facts re-read from the AIG ledger">
      <ol className="timeline">
        {events.map((event) => (
          <li key={event.seq}>
            <span className="t">{fmt.time(event.at)}</span>
            <span className="s">
              <Status tone={OUTCOME_TONE[event.outcome] ?? "info"}>{event.stage}</Status>
            </span>
            <span className="m">
              <span>{event.summary}</span>
              <span className="tiny muted"> · {event.actor}</span>
            </span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export function LedgerPanel({ incident }: { incident: Incident }) {
  const { run, action, receipt } = incident;
  return (
    <Panel title="Durable governance record" sub="Agentic Intent Gate Run/Action ledger + consumer-owned effect receipt">
      {!run ? (
        <div className="small secondary">No governed Run yet.</div>
      ) : (
        <dl className="kv">
          <dt>Run</dt>
          <dd className="mono">
            {run.run_id} · {run.state} v{run.version}
          </dd>
          {action ? (
            <>
              <dt>Action</dt>
              <dd className="mono">
                {action.action_id} · {action.state} v{action.version}
              </dd>
              <dt>Spec digest</dt>
              <dd className="mono">{action.spec_digest}</dd>
              <dt>Effect key</dt>
              <dd className="mono">{action.effect_key}</dd>
              {action.authorization ? (
                <>
                  <dt>Authorization</dt>
                  <dd className="mono">
                    {action.authorization.authorization_id} · {action.authorization.subject_id} ·{" "}
                    {action.authorization.policy_revision}
                  </dd>
                </>
              ) : null}
              {action.result ? (
                <>
                  <dt>Result</dt>
                  <dd>
                    {action.result.code} — {action.result.message}
                  </dd>
                </>
              ) : null}
            </>
          ) : null}
          {receipt ? (
            <>
              <dt>Receipt store</dt>
              <dd className="mono">
                {receipt.state}
                {receipt.receipt ? ` · ${receipt.receipt}` : ""}
              </dd>
              <dt>Claim attempt</dt>
              <dd className="mono">{receipt.attempt_id}</dd>
              <dt>resourceVersion</dt>
              <dd className="mono">
                {receipt.pre_resource_version} → {receipt.post_resource_version ?? "—"}
              </dd>
              <dt>Selector</dt>
              <dd className="mono">
                {receipt.from_selector} → {receipt.observed_selector ?? receipt.to_selector}
              </dd>
              <dt>Executor note</dt>
              <dd>{receipt.detail}</dd>
              <dt>Dispatched</dt>
              <dd className="mono">
                {fmt.time(receipt.dispatched_at)} → {fmt.time(receipt.completed_at)}
              </dd>
            </>
          ) : null}
        </dl>
      )}
    </Panel>
  );
}

export function RedteamPanel({
  catalog,
  refusals,
  onRun,
  canAct,
  busy,
}: {
  catalog: RedteamCatalog;
  refusals: Refusal[];
  onRun: (scenario: string) => void;
  canAct: boolean;
  busy: boolean;
}) {
  return (
    <Panel
      title="Unsafe proposals refused"
      sub="Red-team fixtures (operator-injected, not model output) submitted through the same Agentic Intent Gate validation"
    >
      {canAct ? (
        <div className="row" style={{ marginBottom: 12 }}>
          {Object.entries(catalog).map(([key, scenario]) => (
            <button key={key} className="btn" disabled={busy} onClick={() => onRun(key)} title={scenario.narrative}>
              {scenario.title}
            </button>
          ))}
        </div>
      ) : null}
      <div className="stack">
        {refusals.length === 0 ? <div className="small secondary">No unsafe proposals submitted yet.</div> : null}
        {refusals.slice(0, 6).map((refusal) => (
          <div className="refusal" key={refusal.refusal_id}>
            <div className="spread">
              <Status tone={refusal.run_state === "PlanRejected" ? "critical" : "warning"}>
                {refusal.run_state === "PlanRejected" ? "Refused by AIG" : refusal.run_state} · {refusal.title}
              </Status>
              <span className="tiny muted">{fmt.time(refusal.created_at)}</span>
            </div>
            <div className="tiny muted mono" style={{ marginTop: 4 }}>
              Run {refusal.run_id?.slice(0, 8)} · {refusal.error_code} · {refusal.origin}
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <span className="tiny muted">Gate issue codes</span>
              {refusal.issues
                .filter((issue) => issue.path !== "explanation")
                .slice(0, 6)
                .map((issue, index) => (
                  <span key={index} className="chip mono" title={issue.path}>
                    {issue.code} {issue.path}
                  </span>
                ))}
            </div>
            <ul className="explain">
              {refusal.issues
                .filter((issue) => issue.path === "explanation")
                .map((issue, index) => (
                  <li key={index}>{issue.code}</li>
                ))}
            </ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}
