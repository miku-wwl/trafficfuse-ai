"""In-cluster probe entrypoint.

Usage (see deploy/lab/probe-job.yaml)::

    python -m trafficfuse.lab.probe_cli \\
        --url http://trafficfuse-live.trafficfuse-system.svc.cluster.local/checkout \\
        --scenario-id phase1b-live --target-label service:trafficfuse-live \\
        --requests 40 --concurrency 4 --timeout 5 --output /evidence/probe-live.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from trafficfuse.lab.probe import ProbeConfig, run_probe


def main(argv: list[str] | None = None) -> int:
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
    config = ProbeConfig(
        target_url=f"{base_url}{path}",
        scenario_id=args.scenario_id,
        target_label=args.target_label,
        total_requests=args.requests,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
    )
    evidence = asyncio.run(run_probe(config))
    payload = evidence.to_json()
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
