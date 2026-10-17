"""Guarded lifecycle CLI (offline preflight in Phase 1A).

Subcommands:

* ``preflight`` — validate the future create request, print the non-mutating plan.
* ``create``    / ``destroy`` — always refuse in Phase 1A; the guards run first
  so the refusal reason is precise (missing authorization, missing receipt,
  wrong identity, ...). They become mutating only after Phase 1B authorization.

Examples::

    python -m trafficfuse.lab.lifecycle_cli preflight \\
        --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
    python -m trafficfuse.lab.lifecycle_cli destroy \\
        --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \\
        --receipt .runtime/state/cluster-trafficfuse-lab.json --authorized
"""

from __future__ import annotations

import argparse
import json
import sys

from trafficfuse.lab.lifecycle import (
    SafetyViolation,
    build_preflight_plan,
    plan_cluster_create,
    plan_cluster_destroy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trafficfuse.lab.lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="print a non-mutating plan")
    preflight.add_argument("--kubeconfig", required=True)

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
        "--authorized",
        action="store_true",
        help="explicit authorization flag; destruction still refuses in Phase 1A",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            plan = build_preflight_plan(kubeconfig_path=args.kubeconfig)
            sys.stdout.write(json.dumps(plan.to_dict(), indent=2) + "\n")
        elif args.command == "create":
            plan_cluster_create(kubeconfig_path=args.kubeconfig, authorized=args.authorized)
        else:
            plan_cluster_destroy(
                kubeconfig_path=args.kubeconfig,
                receipt_path=args.receipt,
                authorized=args.authorized,
            )
    except SafetyViolation as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
