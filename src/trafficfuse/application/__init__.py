"""Application services (incident orchestration).

``evidence_collection`` builds labelled evidence bundles and the degradation
policy; ``incidents`` drives detection -> diagnosis -> governed action ->
verification through the Agentic Intent Gate public runtime; ``store`` keeps
incidents and the append-only audit timeline.
"""
