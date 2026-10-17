"""Offline evidence-collection CLI (Phase 1B companion; no cluster access).

Captured Probe Job logs (from ``kubectl logs`` or a saved file) are validated
and stored under ``artifacts/phase1b/<run-id>/``::

    python -m trafficfuse.lab.evidence_cli --logs captured-logs.txt --run-id phase1b-live-001
    kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \\
        --context kind-trafficfuse-lab -n trafficfuse-system logs job/trafficfuse-probe \\
        | python -m trafficfuse.lab.evidence_cli --logs - --run-id phase1b-live-001

Structural problems (missing/truncated/ambiguous/duplicated documents) exit 3;
strict validation failures exit 4; the export summary is printed to stdout as
JSON on success.
"""

from __future__ import annotations

import argparse
import json
import sys

from trafficfuse.lab.evidence import EvidenceFormatError
from trafficfuse.lab.evidence_export import (
    EvidenceValidationError,
    collect_evidence_from_logs,
)

EXIT_FORMAT_ERROR = 3
EXIT_VALIDATION_ERROR = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trafficfuse.lab.evidence_cli")
    parser.add_argument(
        "--logs",
        required=True,
        help="captured probe logs file, or '-' to read stdin",
    )
    parser.add_argument("--run-id", default=None, help="run identifier (default: log file stem)")
    parser.add_argument(
        "--artifacts-root",
        default="artifacts/phase1b",
        help="destination root (default: artifacts/phase1b)",
    )
    args = parser.parse_args(argv)

    if args.logs == "-":
        raw_logs = sys.stdin.read()
        run_id = args.run_id or "stdin-run"
    else:
        from pathlib import Path

        path = Path(args.logs)
        try:
            raw_logs = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sys.stderr.write(f"cannot read logs file {path}: {exc}\n")
            return 1
        run_id = args.run_id or path.stem or "run"

    try:
        result = collect_evidence_from_logs(
            raw_logs, run_id=run_id, artifacts_root=args.artifacts_root
        )
    except EvidenceFormatError as exc:
        sys.stderr.write(f"EVIDENCE FORMAT ERROR: {exc}\n")
        return EXIT_FORMAT_ERROR
    except EvidenceValidationError as exc:
        sys.stderr.write(f"EVIDENCE VALIDATION ERROR: {exc}\n")
        return EXIT_VALIDATION_ERROR
    except (ValueError, FileExistsError) as exc:
        sys.stderr.write(f"EXPORT FAILED: {exc}\n")
        return EXIT_VALIDATION_ERROR

    sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
