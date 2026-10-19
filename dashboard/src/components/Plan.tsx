import { useState } from "react";
import type { Incident, Meta, Plan } from "../types";
import { Panel, Status, fmt } from "./common";

const MODEL_ARGS = new Set(["service", "namespace", "from_version", "to_version"]);

function PlanView({ plan, modelTop }: { plan: Plan; modelTop: boolean }) {
  const step = plan.steps[0];
  const cls = (key: string) => (MODEL_ARGS.has(key) ? "field-model" : "field-host");
  return (
    <pre className="code" aria-label="Exact Plan">
      {"{\n"}
      {"  "}"goal": <span className={modelTop ? "field-model" : ""}>{JSON.stringify(plan.goal)}</span>,{"\n"}
      {"  "}"target": <span className={modelTop ? "field-model" : ""}>{JSON.stringify(plan.target)}</span>,{"\n"}
      {"  "}"risk_class": {JSON.stringify(plan.risk_class)},{"\n"}
      {"  "}"steps": [{"\n"}
      {plan.steps.map((s, index) => (
        <span key={index}>
          {"    { "}"tool_name": {JSON.stringify(s.tool_name)}, "tool_version": {JSON.stringify(s.tool_version)},{"\n"}
          {"      "}"arguments": {"{\n"}
          {Object.entries(s.arguments).map(([key, value]) => (
            <span key={key}>
              {"        "}"{key}": <span className={s === step ? cls(key) : ""}>{JSON.stringify(value)}</span>,{"\n"}
            </span>
          ))}
          {"      }\n    }"}
          {index < plan.steps.length - 1 ? ",\n" : "\n"}
        </span>
      ))}
      {"  ]\n}"}
    </pre>
  );
}

export function PlanPanel({
  incident,
  meta,
  token,
  setToken,
  busy,
  onApprove,
  onReject,
  onAuthorize,
  onExecute,
  onVerify,
}: {
  incident: Incident;
  meta: Meta;
  token: string;
  setToken: (token: string) => void;
  busy: boolean;
  onApprove: (approver: string, reason: string) => void;
  onReject: (approver: string, reason: string) => void;
  onAuthorize: () => void;
  onExecute: () => void;
  onVerify: () => void;
}) {
  const [approver, setApprover] = useState("");
  const [reason, setReason] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const readOnly = meta.read_only || meta.mode === "replay" || incident.replay;
  const { run, action, plan } = incident;
  const shown = run?.plan ?? plan?.plan ?? null;
  const fingerprint = run?.plan_fingerprint ?? plan?.fingerprint ?? null;
  const runState = run?.state;
  const actionState = action?.state;
  const args = shown?.steps[0]?.arguments ?? {};
  const profile = meta.mode === "live" ? "live" : "simulation";

  return (
    <Panel
      title="Remediation plan & governance gates"
      sub="The model proposes; Agentic Intent Gate validates, binds approval to this exact Plan and controls the single dispatch."
    >
      {!shown ? (
        <div className="secondary small">No Plan yet. A Plan is proposed only after a grounded AI diagnosis.</div>
      ) : (
        <div className="stack">
          <div className="spread">
            <div className="row">
              {runState ? (
                <Status tone={runState === "PlanRejected" || runState === "Rejected" ? "critical" : runState === "Approved" ? "good" : "info"}>
                  AIG Run {runState} · v{run?.version}
                </Status>
              ) : null}
              <span className="chip">SIDE_EFFECT · approval required</span>
            </div>
            <div className="legend" style={{ marginTop: 0 }}>
              <span>
                <i style={{ background: "var(--wash-accent)", border: "1px solid var(--accent)" }} />
                chosen by the model
              </span>
              <span>
                <i style={{ background: "var(--surface-3)", border: "1px solid var(--border-strong)" }} />
                bound from evidence by trusted host code
              </span>
            </div>
          </div>
          <PlanView plan={shown} modelTop />
          {fingerprint ? (
            <div className="tiny muted mono break">Plan fingerprint (SHA-256) {fingerprint}</div>
          ) : null}
          {plan?.issues && plan.issues.length ? (
            <div className="refusal">
              <Status tone="critical">Refused by Agentic Intent Gate plan validation</Status>
              <table className="data" style={{ marginTop: 6 }}>
                <tbody>
                  {plan.issues.map((issue, index) => (
                    <tr key={index}>
                      <td className="mono">{issue.path}</td>
                      <td className="mono">{issue.code}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {plan.explanations?.length ? (
                <ul className="explain">
                  {plan.explanations.map((text, index) => (
                    <li key={index}>{text}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {/* Gate 1 — human approval of the exact Plan */}
          <div className="gate">
            <h4>1 · Human approval of this exact Plan</h4>
            {runState === "AwaitingApproval" && !readOnly ? (
              <div className="stack">
                <p>Approval records a decision bound to the Plan fingerprint. It dispatches nothing.</p>
                <div className="row">
                  <label className="field" style={{ flex: "1 1 160px" }}>
                    Approver (local name, not authenticated)
                    <input className="input" value={approver} onChange={(e) => setApprover(e.target.value)} maxLength={48} placeholder="e.g. Incident commander" />
                  </label>
                  <label className="field" style={{ flex: "2 1 220px" }}>
                    Reason
                    <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={512} placeholder="Evidence reviewed: EV-1, EV-2, EV-3" />
                  </label>
                </div>
                <label className="row small">
                  <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />I reviewed Plan{" "}
                  <span className="mono">{fmt.hash(fingerprint, 12)}</span>, its evidence and its preconditions
                </label>
                <div className="row">
                  <button className="btn primary" disabled={busy || !reviewed || !approver.trim()} onClick={() => onApprove(approver.trim(), reason)}>
                    Approve exact Plan
                  </button>
                  <button className="btn danger" disabled={busy || !approver.trim()} onClick={() => onReject(approver.trim(), reason)}>
                    Reject
                  </button>
                </div>
              </div>
            ) : (
              <p>
                {runState === "Approved"
                  ? `Approved by ${incident.approver ?? "operator"} at ${fmt.time(run?.updated_at)} — nothing was dispatched by approving.`
                  : runState === "Rejected"
                    ? "Rejected by a human. Nothing will run."
                    : runState === "PlanRejected"
                      ? "The gate refused this Plan; there is nothing to approve."
                      : "Waiting for a validated Plan."}
              </p>
            )}
          </div>

          {/* Gate 2 — independent authorization */}
          <div className="gate">
            <h4>2 · Independent execution authorization</h4>
            {action?.authorization ? (
              <p>
                Action <span className="mono">{action.action_id.slice(0, 8)}</span> authorized by{" "}
                <b>{action.authorization.subject_id}</b> under <span className="mono">{action.authorization.policy_revision}</span> at{" "}
                {fmt.time(action.authorization.authorized_at)}. Still nothing dispatched.
              </p>
            ) : readOnly ? (
              <p>Read-only replay.</p>
            ) : (
              <div className="stack">
                <p>
                  A separate execution capability (operator-held secret, never sent by the server) admits the approved Run as
                  exactly one Action and grants it. Terminal alternative:{" "}
                  <code>uv run python -m trafficfuse.cli authorize {incident.incident_id} --profile {profile}</code>
                </p>
                <label className="field">
                  Execution capability (kept in memory for this page only)
                  <input
                    className="input wide mono"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="64-hex capability token"
                  />
                </label>
                <div className="row">
                  <button className="btn primary" disabled={busy || runState !== "Approved" || !token} onClick={onAuthorize}>
                    Authorize one Action
                  </button>
                  {runState === "AwaitingApproval" ? (
                    <button className="btn ghost" disabled={busy || !token} onClick={onAuthorize} title="Demonstrates that the gate refuses to skip human approval">
                      Try authorizing before approval
                    </button>
                  ) : null}
                </div>
              </div>
            )}
          </div>

          {/* Gate 3 — one bounded execution */}
          <div className="gate">
            <h4>3 · One bounded execution, then independent proof</h4>
            <p>
              Preconditions checked live by the executor and again atomically by the API server: Service uid{" "}
              <span className="mono">{fmt.hash(args.service_uid, 8)}</span>, resourceVersion{" "}
              <span className="mono">{args.expected_resource_version ?? "—"}</span>, selector{" "}
              <span className="mono">version={args.from_version ?? "?"}</span> →{" "}
              <span className="mono">version={args.to_version ?? "?"}</span>. Budget 5 s after durable claim; an ambiguous
              outcome is terminal Unknown and never retried.
            </p>
            {!readOnly ? (
              <div className="row">
                <button className="btn primary" disabled={busy || actionState !== "Authorized" || !token} onClick={onExecute}>
                  Execute governed switch
                </button>
                {["EXECUTED", "RECOVERED", "NOT_RECOVERED", "VERIFICATION_INCONCLUSIVE", "OUTCOME_UNKNOWN"].includes(incident.stage) ? (
                  <button className="btn" disabled={busy || incident.stage === "VERIFYING"} onClick={onVerify}>
                    Re-verify live Service
                  </button>
                ) : null}
                {token ? (
                  <button className="btn ghost" onClick={() => setToken("")}>
                    Forget capability
                  </button>
                ) : null}
              </div>
            ) : null}
            {actionState && actionState !== "Authorized" ? (
              <div style={{ marginTop: 8 }}>
                <Status tone={actionState === "Succeeded" ? "good" : actionState === "Failed" ? "warning" : "critical"}>
                  Action {actionState}
                  {action?.result?.receipt_reference ? ` · receipt ${action.result.receipt_reference.slice(0, 8)}` : ""}
                </Status>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </Panel>
  );
}
