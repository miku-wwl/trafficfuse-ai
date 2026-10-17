"""Phase 1A/1A.1 failure-lab components (offline implementation).

Contains the synthetic lab workload, deterministic fault injection, the
consumer-owned HTTP probe with strict evidence validation and durable export,
and safety-guarded lifecycle planning with ownership verification. Nothing in
this package mutates Kubernetes infrastructure; cluster creation and the
future ``reroute_service_traffic`` executor belong to later phases
(docs/PHASES.md).
"""
