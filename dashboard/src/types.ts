// Shapes returned by the TrafficFuse FastAPI backend (src/trafficfuse/api).

export type Source = "LIVE_KIND" | "OFFLINE_SIMULATION" | "RECORDED_REPLAY" | "TEST_FIXTURE";

export interface Latency {
  min: number | null;
  p50: number | null;
  p95: number | null;
  max: number | null;
  mean: number | null;
}

export interface RequestRecord {
  seq: number;
  status: number | null;
  outcome: string;
  ms: number;
  version: string | null;
  pod: string | null;
}

export interface ProbeSummary {
  probe_id: string;
  scenario: "live" | "baseline" | "verify";
  target_service: string;
  target_url: string;
  total_requests: number;
  success_count: number;
  error_count: number;
  observed_error_percent: number;
  status_distribution: Record<string, number>;
  served_versions: Record<string, number>;
  served_pods: Record<string, number>;
  latency_ms: Latency;
  started_at_utc: string;
  completed_at_utc: string;
  evidence_sha256: string;
  raw_logs_sha256: string | null;
  artifact_path: string | null;
}

export interface ProbeView extends ProbeSummary {
  integrity: "verified" | "mismatch" | "missing";
  records: RequestRecord[];
}

export interface EvidenceItem {
  evidence_id: string;
  kind: "probe" | "service" | "routing" | "deployment";
  title: string;
  source: Source;
  observed_at: string;
  summary: string;
  data: Record<string, unknown>;
  sha256: string;
}

export interface Bundle {
  schema: string;
  bundle_id: string;
  source: Source;
  collected_at: string;
  cluster: Record<string, string | null>;
  items: EvidenceItem[];
  digest: string;
}

export interface Assessment {
  degraded: boolean;
  baseline_healthy: boolean;
  remediation_justified: boolean;
  live_error_percent: number | null;
  baseline_error_percent: number | null;
  reasons: string[];
  policy: { degraded_error_percent: number; baseline_healthy_error_percent: number };
}

export interface Diagnosis {
  incident_summary: string;
  likely_cause: string;
  evidence_refs: string[];
  confidence: "low" | "medium" | "high";
  uncertainty: string;
  recommended_action: string;
  proposal: Record<string, string>;
}

export interface ModelInfo {
  provider: string;
  model: string;
  model_digest: string | null;
  prompt_version: string;
  duration_seconds: number;
  prompt_tokens: number | null;
  output_tokens: number | null;
  done_reason: string | null;
  raw_output: string;
}

export interface Plan {
  goal: string;
  target: string;
  steps: { tool_name: string; tool_version: string; arguments: Record<string, string> }[];
  risk_class: string;
}

export interface Issue {
  path: string;
  code: string;
}

export interface PlanRecord {
  plan: Plan;
  fingerprint: string;
  model_fields: string[];
  host_fields: string[];
  issues?: Issue[];
  explanations?: string[];
}

export interface Run {
  run_id: string;
  intent: { text: string };
  state: string;
  plan: Plan | null;
  risk_class: string | null;
  version: number;
  approval_required: boolean | null;
  created_at: string;
  updated_at: string;
  error: { code: string; message: string } | null;
  plan_fingerprint?: string;
}

export interface Action {
  action_id: string;
  run_id: string;
  state: string;
  version: number;
  tool_name: string;
  tool_version: string;
  target: string;
  normalized_arguments: Record<string, string>;
  created_at: string;
  updated_at: string;
  authorization: {
    authorization_id: string;
    subject_id: string;
    policy_revision: string;
    authorized_at: string;
  } | null;
  result: { code: string; message: string; receipt_reference: string | null } | null;
  effect_key: string;
  spec_digest: string;
}

export interface Receipt {
  effect_key: string;
  payload_digest: string;
  attempt_id: string;
  state: string;
  receipt: string | null;
  service_uid: string;
  from_selector: string;
  to_selector: string;
  pre_resource_version: string;
  post_resource_version: string | null;
  observed_selector: string | null;
  detail: string;
  created_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Verification {
  verdict: "RECOVERED" | "NOT_RECOVERED" | "INCONCLUSIVE";
  checks: Check[];
  before: ProbeSummary;
  after: ProbeSummary;
  selector_before: Record<string, string>;
  selector_after: Record<string, string>;
  resource_version_before: string;
  resource_version_after: string;
  delta: Record<string, number | null>;
  evidence: Bundle;
  verified_at: string;
}

export interface EventRow {
  seq: number;
  incident_id: string | null;
  at: string;
  stage: string;
  actor: string;
  outcome: string;
  summary: string;
  detail: Record<string, unknown>;
}

export interface Timing {
  detected_at: string;
  diagnosed_at: string | null;
  plan_validated_at: string | null;
  approved_at: string | null;
  authorized_at: string | null;
  executed_at: string | null;
  verified_at: string | null;
  seconds_from_detection: Record<string, number>;
}

export interface Incident {
  incident_id: string;
  created_at: string;
  updated_at: string;
  source: Source;
  stage: string;
  bundle: Bundle;
  bundle_digest: string;
  assessment: Assessment;
  diagnosis: Diagnosis | null;
  model: ModelInfo | null;
  diagnosis_error: string | null;
  plan: PlanRecord | null;
  run_id: string | null;
  action_id: string | null;
  approver: string | null;
  verification: Verification | null;
  run: Run | null;
  action: Action | null;
  receipt: Receipt | null;
  events: EventRow[];
  probes: ProbeView[];
  timing: Timing;
  replay?: boolean;
  recorded_source?: Source;
}

export interface IncidentSummary {
  incident_id: string;
  created_at: string;
  stage: string;
  source: Source;
  live_error_percent: number | null;
  verdict: string | null;
}

export interface Refusal {
  refusal_id: string;
  created_at: string;
  scenario: string;
  title: string;
  origin: string;
  layer: string;
  plan: Plan;
  run_id: string | null;
  run_state: string | null;
  error_code: string | null;
  issues: Issue[];
}

export interface Meta {
  mode: "simulation" | "live" | "replay" | "uninitialized";
  source?: Source;
  source_description?: string;
  read_only?: boolean;
  synthetic_workload?: boolean;
  cluster?: Record<string, string | null>;
  governance?: Record<string, string | number | string[]>;
  aig?: { distribution: string; version: string; commit: string | null; public_module: string; execution_budget_seconds: number };
  model?: { provider: string; name: string; available: boolean };
  recorded?: Record<string, unknown>;
  sources: Record<string, string>;
  version: string;
}

export type RedteamCatalog = Record<string, { title: string; narrative: string }>;
