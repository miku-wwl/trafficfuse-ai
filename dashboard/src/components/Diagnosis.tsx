import type { Incident } from "../types";
import { Panel, Status, fmt } from "./common";

export function DiagnosisPanel({
  incident,
  onFocusEvidence,
  onDiagnose,
  canAct,
  busy,
}: {
  incident: Incident;
  onFocusEvidence: (id: string) => void;
  onDiagnose: () => void;
  canAct: boolean;
  busy: boolean;
}) {
  const { diagnosis, model, stage } = incident;
  const isFixture = model?.done_reason === "fixture";
  const label = model
    ? isFixture
      ? "TEST FIXTURE — not a model output"
      : `Real model output · ${model.model} via ${model.provider}`
    : null;
  return (
    <Panel
      title="AI diagnosis"
      sub={label ?? "Evidence-grounded reasoning by a local model; it can propose, never execute."}
      right={
        canAct && ["DETECTED", "DIAGNOSIS_REJECTED", "MODEL_UNAVAILABLE"].includes(stage) ? (
          <button className="btn primary" onClick={onDiagnose} disabled={busy}>
            {stage === "DETECTED" ? "Diagnose with AI" : "Retry diagnosis"}
          </button>
        ) : null
      }
    >
      {stage === "DIAGNOSING" ? (
        <div className="row secondary">
          <span className="spinner" aria-hidden="true" /> The local model is reasoning over {incident.bundle.items.length} evidence
          items (CPU inference; typically 30–60 s).
        </div>
      ) : null}
      {incident.diagnosis_error && !diagnosis ? (
        <div className="stack">
          <Status tone="critical">
            {stage === "MODEL_UNAVAILABLE" ? "Model unavailable — no diagnosis was fabricated" : "Model output rejected by the grounding validator"}
          </Status>
          <div className="small secondary break">{incident.diagnosis_error}</div>
        </div>
      ) : null}
      {diagnosis ? (
        <div className="stack">
          <div>
            <div className="small muted">Incident summary</div>
            <div style={{ fontSize: 15 }}>{diagnosis.incident_summary}</div>
          </div>
          <div>
            <div className="small muted">Likely cause</div>
            <div>{diagnosis.likely_cause}</div>
          </div>
          <div className="row">
            <span className="small muted">Cited evidence</span>
            {diagnosis.evidence_refs.map((ref) => (
              <span className="chip cited" key={ref}>
                <button onClick={() => onFocusEvidence(ref)} aria-label={`Show ${ref}`}>
                  {ref}
                </button>
              </span>
            ))}
          </div>
          <div className="row">
            <span className="chip">
              Confidence: <b>{diagnosis.confidence}</b>
            </span>
            <span className="chip">
              Recommends: <b>{diagnosis.recommended_action}</b>
            </span>
          </div>
          <div>
            <div className="small muted">Stated uncertainty</div>
            <div className="secondary">{diagnosis.uncertainty}</div>
          </div>
          {model && !isFixture ? (
            <div className="tiny muted">
              {fmt.duration(model.duration_seconds)} · {model.prompt_tokens ?? "?"} prompt / {model.output_tokens ?? "?"} output tokens ·
              digest {fmt.hash(model.model_digest, 12)} · {model.prompt_version}
            </div>
          ) : null}
          <div className="tiny muted">
            The grounding validator confirmed every cited id exists in this bundle and that the output contains no
            authority fields. The model is advisory: the human reviews the exact Plan below.
          </div>
        </div>
      ) : null}
      {model ? (
        <details style={{ marginTop: 10 }}>
          <summary className="small muted" style={{ cursor: "pointer" }}>
            Raw model output (verbatim)
          </summary>
          <pre className="code" style={{ marginTop: 6 }}>
            {model.raw_output}
          </pre>
        </details>
      ) : null}
    </Panel>
  );
}
