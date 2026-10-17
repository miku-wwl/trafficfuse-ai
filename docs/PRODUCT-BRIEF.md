# TrafficFuse AI — Product Brief

**Evidence-Governed AI Release Circuit Breaker** · TechEx Amsterdam Hackathon 2026

## The problem in one paragraph

A dangerous Kubernetes deployment (bad config, incompatible image, resource regression)
becomes customer-facing HTTP 500s within minutes. The correct intervention — shifting
traffic away from the faulty version — is exactly the kind of urgent, high-risk action that
goes wrong under pressure: the wrong service gets targeted, nobody re-checks the plan, the
person who approves is also the person who acts, and there is no durable record of what was
approved and what actually happened.

## Target users

- **SRE / Platform Engineering teams** operating Kubernetes services under error-budget pressure.
- **Incident commanders** deciding whether to roll back or shift traffic during an active degradation.
- **Engineering leadership** that needs an auditable record of which intervention was approved, by whom, and with what result.

## Pain points

- Slow, manual, error-prone mitigation during elevated error rates.
- "AI agents" that either only observe, or act without governance; neither is safe.
- No evidence chain from detection to decision to verified recovery.

## Product differentiation

1. **Governance first.** Human Plan Approval and independent Action Authorization are separate
   gates enforced by a dedicated runtime (Agentic Intent Gate) — not UI theater.
2. **Evidence before action.** The AI Plan cites collected evidence (HTTP probes, deployment
   metadata, Kubernetes state); the reviewer sees the basis, not just the conclusion.
3. **Exactly one bounded action per incident.** A single trusted `SIDE_EFFECT` tool with strict
   argument schema, stable effect keys, idempotent handling, and fail-closed behavior on
   ambiguity — no automatic retry of an uncertain dispatch.
4. **Verified outcome.** The application independently measures service recovery; a recorded
   Action success is never treated as proof of recovery.
5. **Demo integrity.** Synthetic evidence is always labeled. No scripted success is presented
   as a production operation.

## Why agent governance matters

Model output proposes a Plan; it never grants execution permissions. Trusted identities,
credentials, scopes and budgets live only in reviewed host code. Ambiguity after dispatch is
terminal `Unknown` — the system fails closed instead of guessing. This is the difference
between an agent that *recommends* safely and one that *acts* safely.

## Measurable demo success criteria

- Time from detected degradation to governed human decision: minutes, not hours, in the demo.
- 100% of interventions accompanied by durable audit evidence:
  Plan → Approval → Authorization → Action claim → receipt → independent verification.
- Zero out-of-allowlist targets; zero production cluster access — enforced by construction.
- Before/after error-rate measurements demonstrating recovery after the single bounded action.

## Business value

- Reduced MTTR for deployment-caused incidents.
- Bounded blast radius: one controlled action, one allowlisted target, explicit human control.
- Audit-ready incident evidence suitable for post-incident review and compliance conversations.

## Limitations (stated honestly)

- The underlying runtime supports **one trusted SIDE_EFFECT tool per composition and
  single-step execution**; TrafficFuse does not claim general multi-agent orchestration.
- Exactly-once remote effects are not guaranteed; the design fails closed on ambiguity.
- Multi-user authentication, high availability, and production hardening are out of hackathon scope.
- LLM proposals can be wrong. The approval gate is the last line of defense — the model is not.
- The demo runs in an isolated local Kind environment, never against production infrastructure.
