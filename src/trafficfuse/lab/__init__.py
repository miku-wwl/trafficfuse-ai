"""Phase 1A failure-lab components (offline implementation).

Contains the synthetic lab workload, deterministic fault injection, the
consumer-owned HTTP probe, and safety-guarded lifecycle planning. Nothing in
this package mutates Kubernetes infrastructure; cluster creation and the
future ``reroute_service_traffic`` executor belong to later phases
(docs/PHASES.md).
"""
