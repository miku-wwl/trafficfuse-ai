import type { Incident } from "../types";
import { Panel, SourceBadge, Status, fmt } from "./common";

export function EvidencePanel({ incident, focused }: { incident: Incident; focused: string | null }) {
  const cited = new Set(incident.diagnosis?.evidence_refs ?? []);
  return (
    <Panel
      title="Evidence the AI was shown"
      sub={
        <span className="mono">
          bundle {incident.bundle.bundle_id} · sha256 {fmt.hash(incident.bundle.digest, 16)}
        </span>
      }
      right={<SourceBadge source={incident.source} />}
    >
      <div className="stack">
        {incident.bundle.items.map((item) => (
          <div
            key={item.evidence_id}
            id={`evidence-${item.evidence_id}`}
            className="tile"
            style={{
              borderColor: focused === item.evidence_id ? "var(--accent)" : cited.has(item.evidence_id) ? "var(--border-strong)" : undefined,
              boxShadow: focused === item.evidence_id ? "0 0 0 1px var(--accent) inset" : undefined,
            }}
          >
            <div className="spread">
              <div className="row">
                <span className="chip">{item.evidence_id}</span>
                <span className="strong small">{item.title}</span>
                {cited.has(item.evidence_id) ? <span className="chip cited">cited by AI</span> : null}
              </div>
              <span className="tiny muted mono" title={item.sha256}>
                {item.kind} · {fmt.time(item.observed_at)} · {fmt.hash(item.sha256, 10)}
              </span>
            </div>
            <div className="small secondary" style={{ marginTop: 4 }}>
              {item.summary}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Selector({ value }: { value: Record<string, string> | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <span className="mono">
      {Object.entries(value)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ")}
    </span>
  );
}

export function RoutingPanel({ incident }: { incident: Incident }) {
  const service = incident.bundle.items.find((i) => i.kind === "service")?.data as
    | { name: string; namespace: string; uid: string; resource_version: string; selector: Record<string, string> }
    | undefined;
  const endpoints = (incident.bundle.items.find((i) => i.kind === "routing")?.data as { endpoints: { pod: string; ready: boolean }[] })
    ?.endpoints;
  const verification = incident.verification;
  const afterEndpoints = (verification?.evidence.items.find((i) => i.kind === "routing")?.data as {
    endpoints: { pod: string; ready: boolean }[];
  })?.endpoints;
  const deployments = incident.bundle.items.filter((i) => i.kind === "deployment");
  return (
    <Panel title="Kubernetes routing" sub="Target identity, selector and the Pods actually behind the live Service">
      <dl className="kv">
        <dt>Cluster</dt>
        <dd className="mono">
          {incident.bundle.cluster.cluster} · {incident.bundle.cluster.context}
        </dd>
        <dt>Service</dt>
        <dd className="mono">
          {service?.namespace}/{service?.name}
        </dd>
        <dt>UID</dt>
        <dd className="mono">{service?.uid}</dd>
        <dt>Selector at detection</dt>
        <dd>
          <Selector value={service?.selector} /> <span className="muted">(resourceVersion {service?.resource_version})</span>
        </dd>
        <dt>Endpoints at detection</dt>
        <dd className="mono">{endpoints?.map((e) => e.pod).join(", ") || "—"}</dd>
        {verification ? (
          <>
            <dt>Selector after</dt>
            <dd>
              <Selector value={verification.selector_after} />{" "}
              <span className="muted">(resourceVersion {verification.resource_version_after})</span>
            </dd>
            <dt>Endpoints after</dt>
            <dd className="mono">{afterEndpoints?.map((e) => e.pod).join(", ") || "—"}</dd>
          </>
        ) : null}
      </dl>
      <div className="stack" style={{ marginTop: 12 }}>
        {deployments.map((d) => {
          const data = d.data as { name: string; version: string; ready_replicas: number; replicas: number; image: string };
          return (
            <div key={d.evidence_id} className="spread small">
              <span>
                <i
                  style={{
                    display: "inline-block",
                    width: 10,
                    height: 10,
                    borderRadius: 3,
                    marginRight: 6,
                    background: data.version === "healthy" ? "var(--series-healthy)" : "var(--series-faulty)",
                  }}
                />
                {data.name}
              </span>
              <span className="muted mono">
                {data.ready_replicas}/{data.replicas} ready · {data.image}
              </span>
            </div>
          );
        })}
      </div>
      {verification ? (
        <div className="stack" style={{ marginTop: 12 }}>
          {verification.checks.map((check) => (
            <div key={check.name}>
              <Status tone={check.passed ? "good" : "critical"}>{check.name.replace(/_/g, " ")}</Status>
              <div className="tiny muted" style={{ marginLeft: 22 }}>
                {check.detail}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}
