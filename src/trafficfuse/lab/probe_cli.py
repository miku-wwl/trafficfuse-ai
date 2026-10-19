"""In-cluster probe entrypoint.

Usage (see deploy/lab/probe-job.yaml)::

    python -m trafficfuse.lab.probe_cli \\
        --url http://trafficfuse-live.trafficfuse-system.svc.cluster.local/checkout \\
        --scenario-id phase1b-live --target-label service:trafficfuse-live \\
        --requests 40 --concurrency 4 --timeout 5 --output /evidence/probe-live.json

Evidence output (Phase 1A.1, review finding P1):

* When ``--output`` names a writable path, the complete JSON evidence document
  is written there (durable export); a failed export is a hard error.
* The same complete JSON document is **always** printed to stdout, wrapped in
  the unambiguous ``TRAFFICFUSE_EVIDENCE_BEGIN`` / ``TRAFFICFUSE_EVIDENCE_END``
  markers, so automation can extract exactly one document from the Pod logs
  even when the Pod's ephemeral volume is lost.
* **Single ordered stream.** On the success path every line — progress before
  the probe, the export notice and the completion summary — goes to stdout
  *before* the marked document, which is written last. ``kubectl logs``
  merges a container's stdout and stderr line by line in runtime read order,
  so a stderr line can land inside a stdout document (observed in the first
  live Kind run, session ``phase1b-001``). One pipe preserves write order.
  stderr is reserved for fatal configuration/export/validation errors, where
  no document is emitted.
* Evidence is strictly validated before it is written or printed; invalid
  evidence fails closed (exit code 2).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from trafficfuse.lab.evidence import (
    render_stdout_document,
    validate_evidence_document,
)
from trafficfuse.lab.probe import ProbeConfig, run_probe

EXIT_OK = 0
EXIT_INVALID_EVIDENCE = 2
EXIT_OUTPUT_FAILED = 3


def main(argv: list[str] | None = None, *, transport: object | None = None) -> int:
    """Run the probe CLI. ``transport`` is injected in offline tests (ASGI
    transport); in-cluster runs use the default network transport."""
    parser = argparse.ArgumentParser(prog="trafficfuse.lab.probe_cli")
    parser.add_argument("--url", required=True, help="absolute target URL to probe")
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--path", default="/checkout", help="request path appended to --url")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output", default=None, help="write evidence JSON to this file")
    args = parser.parse_args(argv)

    base_url = args.url.rstrip("/")
    path = args.path if args.path.startswith("/") else f"/{args.path}"
    try:
        config = ProbeConfig(
            target_url=f"{base_url}{path}",
            scenario_id=args.scenario_id,
            target_label=args.target_label,
            total_requests=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
        )
    except ValueError as exc:
        sys.stderr.write(f"invalid probe configuration: {exc}\n")
        return 1

    sys.stdout.write(
        f"probing {config.target_url} ({config.total_requests} requests, "
        f"concurrency {config.concurrency})\n"
    )
    sys.stdout.flush()
    evidence = asyncio.run(run_probe(config, transport=transport))
    payload = evidence.to_json()
    document = evidence.to_dict()

    validation = validate_evidence_document(document)
    if not validation.valid:
        sys.stderr.write("REFUSING to emit invalid evidence:\n")
        for error in validation.errors:
            sys.stderr.write(f"  - {error}\n")
        return EXIT_INVALID_EVIDENCE

    if args.output:
        output_path = Path(args.output)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"evidence export FAILED to {output_path}: {exc}\n")
            return EXIT_OUTPUT_FAILED
        sys.stdout.write(f"evidence written to {output_path}\n")

    sys.stdout.write(
        f"probe complete: {evidence.success_count} success / {evidence.error_count} error "
        f"(observed {evidence.observed_error_percent}%)\n"
    )
    # The marked document is the final output: nothing can follow it.
    sys.stdout.write(render_stdout_document(payload))
    sys.stdout.flush()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
