"""Run the real CloudTwin simulation pipeline as a CI risk threshold gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from simulation.pipeline import simulate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--twin", required=True, type=Path)
    parser.add_argument("--change", required=True, type=Path)
    parser.add_argument("--kind", choices=["terraform", "kubernetes", "scenario"])
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--fail-at", type=int, default=80)
    args = parser.parse_args()

    missing = [str(path) for path in (args.twin, args.change) if not path.is_file()]
    if missing:
        parser.error(
            "real PR analysis inputs are missing: " + ", ".join(missing)
            + ". Provide a discovered Current Twin and the proposed Terraform plan JSON "
              "or Kubernetes manifest; synthetic scenarios are not accepted by the PR gate."
        )

    output = simulate(args.twin, args.change, kind=args.kind)
    output.pop("_context", None)
    result = output.pop("_result")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    score = result.risk["score"]
    coverage = output.get("changes", {}).get("metadata", {}).get("terraform_coverage")
    if coverage and coverage.get("status") != "complete":
        print(
            "CloudTwin risk analysis incomplete: unsupported material Terraform "
            f"resources={len(coverage.get('unsupported_material', []))}; refusing a safe PASS."
        )
        return 2
    print(f"CloudTwin risk score: {score}/100; failure threshold: {args.fail_at}")
    return 1 if score >= args.fail_at else 0


if __name__ == "__main__":
    sys.exit(main())
