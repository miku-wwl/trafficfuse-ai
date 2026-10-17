# TrafficFuse AI — Competition Alignment

**Event:** TechEx Amsterdam Hackathon — AI & Big Data Expo Europe
**Official page:** https://lablab.ai/ai-hackathons/techex-amsterdam-hackathon
**Organizers:** TechEx Events and lablab.ai

## Verified from the official page (checked 2026-10-17)

- **Format:** Hybrid. **Online build: October 16–19, 2026. On-site: October 19–20, 2026**
  at RAI Amsterdam (Europaplein 24, 1078 GZ Amsterdam).
- **Audience:** open worldwide; participation via the lablab.ai platform and Discord
  ("Enroll" + Hackathon Guidelines + Getting Started Guide).
- **On-site:** showcasing is subject to approval; travel and accommodation are not covered.
- **Judging criteria (confirmed):**
  - Application of Technology
  - Presentation
  - Business Value
  - Originality
- **Submission materials ("What to submit?"):**
  - Project title, short description, long description, technology & category tags
  - Cover image
  - Video presentation
  - Slide presentation
  - Public GitHub repository
  - Demo application platform
  - Application URL
- Submission guidance reference: https://lablab.ai/delivering-your-hackathon-solution

## Unknown / pending requirements

- **Final submission deadline timestamp:** the page publishes the event window
  (online build Oct 16–19, on-site Oct 19–20) but no precise submission cut-off at the time
  of writing. **Re-check before relying on any date.**
- **Prizes:** categories/awards "coming soon" — cash prizes, technology credits, conference
  benefits, mentorship and visibility are *potential*, not confirmed.
- **Eligibility details** beyond "open worldwide" are not published on the page.
- **Sponsor technology tracks/challenges:** to be announced.
- **Reuse-of-prior-code policy:** not stated on the event page; general rules live in the
  lablab.ai Hackathon Guidelines. Assume disclosure is required (see below) until verified.

## Prior-code dependency disclosure

- **Agentic Intent Gate v0.2.0** (https://github.com/miku-wwl/agentic-intent-gate, MIT) is a
  **pre-existing runtime** consumed as a pinned installed dependency
  (`agentic-intent-gate==0.2.0`, commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`). It
  supplies Plan validation, approval binding, authorization, durable action claims, ledger,
  idempotency and fail-closed outcome handling. **This is pre-existing work and is disclosed
  as such in the repository (AGENTS.md, README.md, docs/ARCHITECTURE.md).**
- **All TrafficFuse-specific code** — evidence collection, incident reasoning, consumer
  ModelProvider, Kubernetes executor, target allowlists, verification, UI — is new work
  developed for this hackathon. No other third-party or sponsor code is used.

## Submission readiness checklist

- [x] Public GitHub repository exists: `miku-wwl/trafficfuse-ai` (kept free of secrets,
      kubeconfigs, runtime databases)
- [ ] Cover image
- [ ] Video presentation (Phase 5)
- [ ] Slide presentation (Phase 5)
- [ ] Demo application platform / application URL (Phase 4–5)
- [ ] Project title / descriptions / category tags (Phase 5)
- [ ] Re-verify official submission deadline and guidelines before submitting

## Compliance risks

1. **Unknown submission deadline** — highest-priority unknown; monitor the official page and
   the lablab.ai submission guidelines.
2. **Reuse disclosure** — the shared runtime is intentionally reused (as the event's
   technical direction expects from this project); it is disclosed in-repository, but the
   submission form's exact wording should be double-checked against official guidance.
3. **Demo integrity** — the live demo must use a real isolated Kind environment; any
   synthetic evidence must be visibly labeled (AGENTS.md rule).
