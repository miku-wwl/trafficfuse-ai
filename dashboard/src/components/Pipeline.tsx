import type { Incident } from "../types";
import { Icon, Panel, fmt } from "./common";

type StepState = "done" | "active" | "failed" | "blocked" | "pending";

interface Step {
  title: string;
  owner: string;
  state: StepState;
  detail: string;
}

function steps(incident: Incident): Step[] {
  const { run, action, receipt, verification, diagnosis, model, stage } = incident;
  const runState = run?.state;
  const actionState = action?.state;
  return [
    {
      title: "Evidence collected",
      owner: "TrafficFuse probes",
      state: "done",
      detail: `${incident.bundle.items.length} items · bundle ${fmt.hash(incident.bundle.digest, 10)}`,
    },
    {
      title: "AI diagnosis",
      owner: model?.model ?? "local model",
      state: diagnosis
        ? "done"
        : stage === "DIAGNOSING"
          ? "active"
          : ["DIAGNOSIS_REJECTED", "MODEL_UNAVAILABLE"].includes(stage)
            ? "failed"
            : stage === "DETECTED"
              ? "active"
              : "pending",
      detail: diagnosis
        ? `${diagnosis.recommended_action} · ${diagnosis.confidence} confidence`
        : stage === "DIAGNOSING"
          ? "reasoning over evidence…"
          : incident.diagnosis_error ?? "awaiting diagnosis",
    },
    {
      title: "Plan validated",
      owner: "Agentic Intent Gate",
      state: !run ? "pending" : runState === "PlanRejected" ? "failed" : "done",
      detail: run
        ? runState === "PlanRejected"
          ? `rejected: ${run.error?.code ?? "invalid"}`
          : `Run ${run.run_id.slice(0, 8)} persisted`
        : "no proposal yet",
    },
    {
      title: "Human approval",
      owner: incident.approver ?? "reviewer",
      state:
        runState === "Approved"
          ? "done"
          : runState === "Rejected"
            ? "failed"
            : runState === "AwaitingApproval"
              ? "active"
              : "pending",
      detail:
        runState === "Approved"
          ? `exact Plan ${fmt.hash(run?.plan_fingerprint, 8)} approved`
          : runState === "AwaitingApproval"
            ? "awaiting a human decision"
            : runState === "Rejected"
              ? "rejected by a human"
              : "—",
    },
    {
      title: "Independent authorization",
      owner: action?.authorization?.subject_id ?? "capability holder",
      state: action?.authorization ? "done" : runState === "Approved" ? "active" : "pending",
      detail: action?.authorization
        ? `grant ${action.authorization.authorization_id.slice(0, 8)} · ${action.authorization.policy_revision}`
        : runState === "Approved"
          ? "approval alone dispatches nothing"
          : "—",
    },
    {
      title: "Durable claim + execution",
      owner: "AIG gateway → executor",
      state:
        actionState === "Succeeded"
          ? "done"
          : actionState === "Failed" || actionState === "Unknown"
            ? "failed"
            : actionState === "Authorized"
              ? "active"
              : stage === "EXECUTING"
                ? "active"
                : "pending",
      detail:
        actionState === "Succeeded"
          ? `one dispatch · attempt ${receipt?.attempt_id.slice(0, 8) ?? ""}`
          : actionState === "Failed"
            ? "confirmed no effect"
            : actionState === "Unknown"
              ? "outcome UNKNOWN — never retried"
              : actionState === "Authorized"
                ? "ready for one bounded dispatch"
                : "—",
    },
    {
      title: "Durable receipt",
      owner: "consumer receipt store",
      state: receipt?.state === "SUCCEEDED" ? "done" : receipt && receipt.state !== "SUCCEEDED" ? "failed" : "pending",
      detail: receipt ? `${receipt.state} · ${receipt.receipt?.slice(0, 8) ?? receipt.detail}` : "—",
    },
    {
      title: "Independent verification",
      owner: "TrafficFuse probes",
      state:
        verification?.verdict === "RECOVERED"
          ? "done"
          : verification?.verdict
            ? "failed"
            : stage === "VERIFYING"
              ? "active"
              : "pending",
      detail: verification
        ? `${verification.verdict}: ${fmt.pct(verification.before.observed_error_percent)} → ${fmt.pct(verification.after.observed_error_percent)}`
        : stage === "VERIFYING"
          ? "re-probing the live Service…"
          : "API success is not proof",
    },
  ];
}

const TONE: Record<StepState, "good" | "critical" | "warning" | "pending" | "info"> = {
  done: "good",
  active: "info",
  failed: "critical",
  blocked: "warning",
  pending: "pending",
};

export function PipelinePanel({ incident }: { incident: Incident }) {
  return (
    <Panel
      title="Governed remediation chain"
      sub="Plan → Run → exact Approval → Admission → independent Authorization → durable claim → executor → receipt → proof"
    >
      <ol className="pipeline" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {steps(incident).map((step, index) => (
          <li key={step.title} className={`step ${step.state}`}>
            <div className="spread">
              <span className="step-index">STEP {index + 1}</span>
              <Icon tone={TONE[step.state]} />
            </div>
            <div className="step-title">{step.title}</div>
            <div className="step-detail">{step.detail}</div>
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {step.owner}
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
