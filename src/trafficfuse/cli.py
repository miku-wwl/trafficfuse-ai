"""Operator CLI (trusted local host only).

    uv run python -m trafficfuse.cli provision --profile live
    uv run python -m trafficfuse.cli authorize INC-... [--profile live]
    uv run python -m trafficfuse.cli execute   INC-... [--profile live]
    uv run python -m trafficfuse.cli export-replay INC-... [--out demo/replay/recorded-incident.json]

``authorize``/``execute`` model separation of duties: the execution capability
is read from the private profile file and sent once as a bearer header to the
local API. It is never printed. Approval happens elsewhere (the dashboard);
holding this capability does not approve anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from trafficfuse.config.product import ProductSettings
from trafficfuse.domain.evidence import SOURCE_REPLAY
from trafficfuse.infrastructure.governance import CAPABILITY_FILE, provision_profile

DEFAULT_API = "http://127.0.0.1:8000"


def _profile_dir(profile: str) -> Path:
    return ProductSettings().state_root / profile / "aig"


def _call(api: str, method: str, path: str, **kwargs) -> dict:
    with httpx.Client(
        base_url=api, timeout=60, trust_env=False, headers={"X-TrafficFuse-Client": "cli"}
    ) as client:
        response = client.request(method, path, **kwargs)
    body = response.json()
    if response.status_code >= 400:
        detail = body.get("detail", body)
        raise SystemExit(f"REFUSED ({response.status_code}): {json.dumps(detail)}")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trafficfuse.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    provision = sub.add_parser("provision", help="provision a new private governance profile")
    provision.add_argument("--profile", choices=("live", "simulation"), required=True)
    for name in ("authorize", "execute"):
        command = sub.add_parser(name, help=f"{name} the incident's Action with the execution capability")
        command.add_argument("incident_id")
        command.add_argument("--profile", choices=("live", "simulation"), default="live")
        command.add_argument("--api", default=DEFAULT_API)
    export = sub.add_parser("export-replay", help="export a recorded incident for read-only replay")
    export.add_argument("incident_id")
    export.add_argument("--api", default=DEFAULT_API)
    export.add_argument("--out", default="demo/replay/recorded-incident.json")
    args = parser.parse_args(argv)

    if args.command == "provision":
        binding = provision_profile(_profile_dir(args.profile), args.profile)
        print(json.dumps({"provisioned": args.profile, "adapter_id": binding.adapter_id,
                          "binding_revision": binding.binding_revision,
                          "execution_scope": binding.execution_scope}, indent=2))
        return 0

    if args.command in ("authorize", "execute"):
        token = (_profile_dir(args.profile) / CAPABILITY_FILE).read_text("ascii").strip()
        body = _call(
            args.api, "POST", f"/api/incidents/{args.incident_id}/{args.command}",
            headers={"Authorization": f"Bearer {token}"},
        )
        action = body.get("action") or {}
        print(json.dumps({"incident": body["incident_id"], "stage": body["stage"],
                          "action_state": action.get("state"),
                          "receipt": (action.get("result") or {}).get("receipt_reference")}, indent=2))
        return 0

    if args.command == "export-replay":
        incident = _call(args.api, "GET", f"/api/incidents/{args.incident_id}")
        meta = _call(args.api, "GET", "/api/meta")
        if meta.get("mode") != "live":
            print("refusing: only live-Kind incidents are exported as recorded evidence", file=sys.stderr)
            return 2
        recorded = {
            "meta": {
                "exported_from_mode": meta["mode"],
                "source_label_when_replayed": SOURCE_REPLAY,
                "cluster": meta.get("cluster"),
                "governance": meta.get("governance"),
                "aig": meta.get("aig"),
                "model": meta.get("model"),
            },
            "incidents": [incident],
            "refusals": _call(args.api, "GET", "/api/refusals"),
            "audit": {"events": [e for e in _call(args.api, "GET", "/api/audit")["events"]
                                 if e.get("incident_id") in (None, args.incident_id)]},
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
