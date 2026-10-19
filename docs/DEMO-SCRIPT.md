# TrafficFuse AI — 3-Minute Demo Script & Storyboard

Audience: hackathon judges (Application of Technology, Presentation, Business
Value, Originality). One continuous story, recorded against the **live** Kind lab
(`TRAFFICFUSE_MODE=live`). Operational steps: [LIVE-DEMO-RUNBOOK.md](LIVE-DEMO-RUNBOOK.md).

**Rules for the recording.** Show only what actually runs. The workload is a
synthetic lab service (say so once). Do not claim production use, measured MTTR
savings or ROI. Model inference on CPU takes 20–40 s: cut the wait, and put a small
"sped up" caption on the cut. Never show the capability token: paste it from the
clipboard into the password field, or use the CLI.

## Storyboard (≈3:00)

| Time | Screen | Narration (suggested) |
| --- | --- | --- |
| 0:00–0:15 | Dashboard header: **LIVE KIND**, synthetic-workload badge, AIG 0.2.0, phi4-mini | "A new release is failing one in two checkouts. Today most AI agents either only *tell* you, or they *act* with your credentials and hope. TrafficFuse is the AI SRE that has to *prove* it fixed the incident." |
| 0:15–0:35 | Click **Probe live Service now** → hero shows 50%, request strip half red, 40/40 served by `faulty`, baseline 0% | "TrafficFuse probes the real Kubernetes Service with 40 real HTTP requests — and the healthy version as a baseline. Every request records which version actually served it. Each evidence document is SHA-256 audited." |
| 0:35–1:00 | Click **Diagnose with AI**; diagnosis panel fills: summary, cause, cited EV chips, confidence, uncertainty, raw output | "A local model — phi4-mini via Ollama, nothing leaves the laptop — reasons over six numbered evidence items. TrafficFuse rejects any answer that cites evidence that doesn't exist, or tries to hand itself credentials. Note the stated uncertainty: the model is advisory." |
| 1:00–1:20 | Plan panel: exact Plan, blue fields = model, dotted = evidence facts; gate status `AwaitingApproval`; fingerprint | "The AI's proposal becomes a Plan validated by Agentic Intent Gate: one tool, one target, one step. The model chose *what* to do; the Service UID and resourceVersion come from evidence, so the Plan only works on the exact state we observed." |
| 1:20–1:35 | Type approver role, tick fingerprint box, **Approve exact Plan** → "nothing was dispatched by approving" | "A human approves this exact Plan — bound to its fingerprint. Approval alone changes nothing." |
| 1:35–1:55 | Second terminal: `uv run python -m trafficfuse.cli authorize INC-… --profile live` → dashboard shows Authorized | "Execution needs a *separate* authorization — a capability held by an operator, not by the AI and not by the reviewer's click. Still nothing has changed in the cluster." |
| 1:55–2:25 | Click **Execute governed switch** → step 6–8 turn green; hero 50% → 0%; after-strip all green; 40/40 served by `healthy`; 4/4 checks | "One durable claim, one guarded change: the Service selector moves from faulty to healthy only if UID, resourceVersion and selector still match. Then TrafficFuse re-measures the same Service: errors fifty percent to zero, every request served by the healthy version. A Kubernetes '200 OK' is never accepted as proof." |
| 2:25–2:45 | Click **Send live traffic to the faulty version** and **Proposal grants itself … kubeconfig** → red refusal cards with reasons | "What if a proposal is unsafe — wrong direction, or trying to grant itself a kubeconfig and a longer budget? The gate refuses it before any human sees an approve button." |
| 2:45–3:00 | Ledger + timeline panels | "Every decision is durable: Plan, approval, authorization, claim, receipt, verification. That's an AI SRE an enterprise can audit. TrafficFuse is built on the pre-existing open-source Agentic Intent Gate runtime; the evidence, AI reasoning, Kubernetes executor and proof of recovery are new for this hackathon." |

## Shot list / checklist before recording

- [ ] Lab reset done; selector `version=faulty` (runbook §2/§4).
- [ ] Server in **live** mode; header shows LIVE KIND, model ready.
- [ ] Ollama warm (one throwaway diagnosis earlier in the session shortens the first call).
- [ ] Capability copied to clipboard (`Get-Content … | Set-Clipboard`) or second terminal ready with the CLI command.
- [ ] Browser zoom ~90% at 1920×1080; dark theme; notifications off.
- [ ] No personal names, tokens, file paths with user names, or foreign clusters on screen.
- [ ] After recording: reset the lab again if you plan another take.

## Alternative: replay-only video

If the live lab is unavailable, record `TRAFFICFUSE_MODE=replay` and say clearly:
"This is a recorded replay of a live run captured on 19 October 2026." The banner
and badges already state it.

## One-paragraph spoken summary (for Q&A)

TrafficFuse turns a release incident into a governed, evidence-backed decision.
It measures the live Service, asks a local model to explain the incident and
propose one bounded fix, validates that proposal in a dedicated governance
runtime, requires a human to approve the exact Plan, requires a separate
capability to authorize execution, performs one guarded Kubernetes change with a
durable receipt, and only declares success after independently re-measuring the
same Service.
