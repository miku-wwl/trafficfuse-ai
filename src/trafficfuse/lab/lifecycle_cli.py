"""Guarded lifecycle CLI (offline preflight and ownership verification).

Subcommands:

* ``preflight`` — validate the future create request, print the non-mutating plan.
* ``verify``    — offline ownership verification: dedicated kubeconfig document,
  creation record and caller-supplied live cluster facts (captured with
  context-pinned read-only commands) are cross-checked. Prints the per-check
  result; never contacts a cluster.
* ``create`` / ``destroy`` — always refuse in Phase 1A; the guards run first so
  the refusal reason is precise (missing authorization, missing/invalid record,
  unverified ownership, wrong identity, ...). They become mutating only after
  Phase 1B authorization.

Every future kubectl invocation built by this project carries the dedicated
kubeconfig and the exact context explicitly (``lifecycle.kubectl_prefix()``);
the ambient context is never read.

Examples::

    python -m trafficfuse.lab.lifecycle_cli preflight \\
        --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
    python -m trafficfuse.lab.lifecycle_cli verify \\
        --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \\
        --receipt .runtime/state/cluster-trafficfuse-lab.json \\
        --facts cluster-facts.json
    python -m trafficfuse.lab.lifecycle_cli destroy \\
        --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \\
        --receipt .runtime/state/cluster-trafficfuse-lab.json --authorized
"""

from __future__ import annotations

import argparse
import json
import sys

from trafficfuse.lab.lifecycle import (
    ClusterFacts,
    SafetyViolation,
    build_preflight_plan,
    plan_cluster_create,
    plan_cluster_destroy,
    verify_cluster_ownership,
)


def _load_facts(path: str) -> ClusterFacts:
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyViolation(f"unreadable cluster facts file {path}: {exc}") from exc
    try:
        return ClusterFacts(
            api_server=data["api_server"],
            ca_fingerprint_sha256=data["ca_fingerprint_sha256"],
            node_names=tuple(data["node_names"]),
            namespace_labels=data.get("namespace_labels") or {},
        )
    except KeyError as exc:
        raise SafetyViolation(f"cluster facts file is missing field {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trafficfuse.lab.lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="print a non-mutating plan")
    preflight.add_argument("--kubeconfig", required=True)

    verify = subparsers.add_parser("verify", help="offline ownership verification")
    verify.add_argument("--kubeconfig", required=True)
    verify.add_argument("--receipt", required=True)
    verify.add_argument(
        "--facts",
        required=True,
        help="JSON file with live cluster facts (api_server, ca_fingerprint_sha256, "
        "node_names, namespace_labels) captured by read-only, context-pinned commands",
    )

    create = subparsers.add_parser("create", help="refuses in Phase 1A (guarded)")
    create.add_argument("--kubeconfig", required=True)
    create.add_argument(
        "--authorized",
        action="store_true",
        help="explicit authorization flag; creation still refuses in Phase 1A",
    )

    destroy = subparsers.add_parser("destroy", help="refuses in Phase 1A (guarded)")
    destroy.add_argument("--kubeconfig", required=True)
    destroy.add_argument("--receipt", required=True)
    destroy.add_argument(
        "--facts",
        default=None,
        help="optional live cluster facts for ownership verification before refusal",
    )
    destroy.add_argument(
        "--authorized",
        action="store_true",
        help="explicit authorization flag; destruction still refuses in Phase 1A",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            plan = build_preflight_plan(kubeconfig_path=args.kubeconfig)
            sys.stdout.write(json.dumps(plan.to_dict(), indent=2) + "\n")
        elif args.command == "verify":
            identity, verification = verify_cluster_ownership(
                kubeconfig_path=args.kubeconfig,
                record_path=args.receipt,
                facts=_load_facts(args.facts),
            )
            payload = {
                "identity": {
                    "context": identity.context,
                    "cluster": identity.cluster,
                    "api_server": identity.api_server,
                    "ca_fingerprint_sha256": identity.ca_fingerprint_sha256,
                },
                **verification.to_dict(),
            }
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            if not verification.verified:
                sys.stderr.write("REFUSED: ownership could not be verified\n")
                return 2
        elif args.command == "create":
            plan_cluster_create(kubeconfig_path=args.kubeconfig, authorized=args.authorized)
        else:
            plan_cluster_destroy(
                kubeconfig_path=args.kubeconfig,
                receipt_path=args.receipt,
                authorized=args.authorized,
                cluster_facts=_load_facts(args.facts) if args.facts else None,
            )
    except SafetyViolation as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
