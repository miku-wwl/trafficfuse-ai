# TrafficFuse AI — Submission Readiness

Checked 2026-10-19 (Pacific/Auckland). Event: TechEx Amsterdam Hackathon 2026
(lablab.ai). Requirements from the official page, re-read 2026-10-19.

```text
SUBMISSION_READINESS=PARTIAL
```

## Deadline — act now

The public event page lists the online build as **16–19 October 2026** and the
on-site days as 19–20 October, but the submission cut-off is **"To be announced"**
(no date, hour or timezone). Today is the last online build day. **Open the
authenticated lablab.ai portal and confirm the exact cut-off before anything else.**

## Required submission items

| Item | Status | Where / next step |
| --- | --- | --- |
| Project title | Ready | "TrafficFuse AI" — [PITCH.md](PITCH.md) |
| Short description | Ready | [PITCH.md](PITCH.md#short-description-250-characters) |
| Long description | Ready | [PITCH.md](PITCH.md#long-description) |
| Technology & category tags | Ready | [PITCH.md](PITCH.md#technology--category-tags) |
| Cover image | Candidate ready | `docs/assets/cover-1920x1080.png` (real live dashboard, 1920×1080). Owner to approve. |
| Video presentation | **Not recorded** | Script + storyboard: [DEMO-SCRIPT.md](DEMO-SCRIPT.md); operations: [LIVE-DEMO-RUNBOOK.md](LIVE-DEMO-RUNBOOK.md). The lab is reset and ready. |
| Slide presentation | **Content ready, file not built** | 10-slide content + diagram spec in [PITCH.md](PITCH.md#slide-deck-content-10-slides) |
| Public GitHub repository | Exists; **new work not committed/pushed** | Owner to review the working tree and authorize commit + push |
| Demo application platform | Decision needed | Local live demo (recorded). For a public URL use **replay mode only** (read-only). |
| Application URL | **None** | Requires a hosting decision and authorization; never expose live/simulation mutating routes publicly |

## Evidence & demo verification checklist

- [x] Real Kind lab created with the dedicated kubeconfig/context; ownership verified live.
- [x] Healthy baseline and faulty live-Service evidence measured with real HTTP (40 requests each), SHA-256 audited.
- [x] Per-request routing evidence (which version/Pod served each request).
- [x] Real local model diagnosis (phi4-mini) on live evidence; grounding validator passed.
- [x] AIG Plan validation → exact-Plan human approval → separate capability authorization → durable claim → one executor dispatch → durable receipt, on the live cluster.
- [x] Independent post-action probe of the same Service: 50% → 0% errors, 40/40 served by `healthy`.
- [x] Four unsafe proposals refused by the gate (tests and live).
- [x] Dashboard works end-to-end through real buttons (headless UI smoke test PASS).
- [x] Read-only replay of the recorded live incident; writes refused.
- [x] Every surface labels live vs simulation vs replay vs fixture; synthetic workload declared.
- [x] 554 offline tests pass; dashboard typecheck/build clean; CSRF/Host guards verified on the live server.
- [ ] Video recorded and uploaded.
- [ ] Slides exported.
- [ ] Working tree committed and pushed.
- [ ] (Optional) read-only replay hosted, with authorization.
- [ ] Deadline confirmed; submission form completed by the owner.

## Disclosure text (paste into the submission)

> TrafficFuse reuses **Agentic Intent Gate v0.2.0**
> (github.com/miku-wwl/agentic-intent-gate, MIT), a pre-existing governance runtime by
> the same author, as a pinned dependency (commit 7261fdd2). It provides Plan
> validation, persistent Runs, exact approval binding, independent authorization,
> durable claims and fail-closed outcomes. All TrafficFuse code — Kubernetes lab and
> evidence, AI diagnosis and grounding, the reroute executor and receipts, recovery
> verification, API and dashboard — was built for this hackathon. The demo runs a
> synthetic workload in a local Kind cluster; all measurements are real HTTP responses.

## What must not be claimed

- No production traffic, customers or enterprise MTTR/ROI measurements.
- No hosted application or published video until they exist.
- The approver identity is a local name (not authenticated SSO).
- Simulation or replay output is never "live".
