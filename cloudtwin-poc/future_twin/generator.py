"""Future Twin generator engine."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from future_twin.models import FutureTwin, InvalidMutationError
from future_twin.mutator import ResourceMutator
from ingestion.ingester import ChangeIngester
from ingestion.models import ChangeSet, NormalizedChange


class FutureTwinGenerator:
    """Produces virtual Future Digital Twins from a Current Twin and proposed changes."""

    def __init__(self, strict_conflicts: bool = False) -> None:
        self.mutator = ResourceMutator(strict_conflicts=strict_conflicts)

    def generate(
        self,
        current_twin: dict[str, Any] | str | Path,
        changes: Sequence[NormalizedChange] | ChangeSet | dict[str, Any] | str | Path,
        simulation_id: str | None = None,
    ) -> FutureTwin:
        """Apply normalized proposed changes to a clean copy of the Current Twin.

        Args:
            current_twin: Current Twin snapshot dict or file path.
            changes: NormalizedChange records, ChangeSet, or raw change input
                     (scenario, terraform plan, or kubernetes manifest).
            simulation_id: Optional unique simulation identifier.

        Returns:
            FutureTwin instance with updated resources, relationships, and provenance.
        """
        raw_twin = self._load_twin(current_twin)

        # IMMUTABILITY GUARANTEE: Deep copy ensures current_twin is never modified.
        future_data = copy.deepcopy(raw_twin)

        # Normalize changes if input is raw scenario / plan / manifest
        change_records = self._resolve_changes(changes, raw_twin)

        provenance: dict[str, list[dict[str, Any]]] = {}
        applied_changes: list[NormalizedChange] = []

        sim_id = simulation_id or f"sim-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        # Deterministic sequential application of changes
        for change in change_records:
            self.mutator.apply_change(future_data, change, provenance)
            applied_changes.append(change)

        # Re-synchronize summary counts
        resources = future_data.get("resources", [])
        relationships = future_data.get("relationships", [])

        aws_count = sum(1 for r in resources if r.get("provider") == "aws")
        k8s_count = sum(1 for r in resources if r.get("provider") == "kubernetes")

        future_data["summary"] = {
            "aws_resources": aws_count,
            "kubernetes_resources": k8s_count,
            "relationships": len(relationships),
        }

        # Deterministic sorting of resources by ID to guarantee identical repeated output
        future_data["resources"] = sorted(resources, key=lambda r: r.get("id", ""))
        future_data["relationships"] = sorted(
            relationships,
            key=lambda rel: (rel.get("source", ""), rel.get("target", ""), rel.get("type", "")),
        )

        return FutureTwin(
            twin=future_data,
            provenance=provenance,
            changes_applied=applied_changes,
            simulation_id=sim_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "current_provider": raw_twin.get("metadata", {}).get("provider"),
                "current_region": raw_twin.get("metadata", {}).get("region"),
                "current_cluster": raw_twin.get("metadata", {}).get("cluster"),
                "total_changes_applied": len(applied_changes),
            },
        )

    @classmethod
    def _resolve_changes(
        cls,
        changes: Sequence[NormalizedChange] | ChangeSet | dict[str, Any] | str | Path,
        current_twin: dict[str, Any],
    ) -> list[NormalizedChange]:
        if isinstance(changes, ChangeSet):
            return list(changes.changes)

        if isinstance(changes, (list, tuple)):
            if all(isinstance(c, NormalizedChange) for c in changes):
                return list(changes)

        # Raw input: delegate to ChangeIngester
        ingester = ChangeIngester(current_twin=current_twin)
        change_set = ingester.ingest(changes)
        return list(change_set.changes)

    @staticmethod
    def _load_twin(twin: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(twin, dict):
            return twin
        if isinstance(twin, Path):
            return json.loads(twin.read_text(encoding="utf-8"))
        if isinstance(twin, str):
            try:
                p = Path(twin)
                if p.is_file():
                    return json.loads(p.read_text(encoding="utf-8"))
            except OSError:
                pass
            return json.loads(twin)
        raise InvalidMutationError(f"Unsupported Current Twin type: {type(twin)}")


def _run_cli() -> int:
    import argparse
    root_dir = Path(__file__).resolve().parents[1]
    default_current = root_dir / "fixtures" / "sample_current_state.json"
    default_scenario = root_dir / "scenarios" / "scenario-01.json"
    default_out = root_dir / "outputs" / "future_state.json"

    parser = argparse.ArgumentParser(description="Generate virtual Future Digital Twin from proposed changes.")
    parser.add_argument(
        "--current", "-c",
        type=Path,
        default=default_current,
        help="Path to Current Twin snapshot fixture",
    )
    parser.add_argument(
        "--changes", "-m",
        type=Path,
        default=default_scenario,
        help="Path to scenario JSON, Terraform plan JSON, or Kubernetes manifest",
    )
    parser.add_argument(
        "--out", "-o",
        type=Path,
        default=default_out,
        help="Output path for generated Future Twin JSON",
    )
    parser.add_argument(
        "--envelope",
        action="store_true",
        help="Save integration contract envelope (including provenance & changes) instead of pure snapshot",
    )

    args = parser.parse_args()

    generator = FutureTwinGenerator()
    print(f"[*] Ingesting Current Twin: {args.current}")
    print(f"[*] Applying proposed changes from: {args.changes}")

    future = generator.generate(
        current_twin=args.current,
        changes=args.changes,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.envelope:
        payload = future.to_envelope()
    else:
        payload = future.to_dict(strict_schema=True)

    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"[OK] Future Twin generated successfully: {args.out}")
    print(f"     Changes applied: {len(future.changes_applied)}")
    print(f"     Resources: {len(future.twin['resources'])} (AWS: {future.twin['summary']['aws_resources']}, K8s: {future.twin['summary']['kubernetes_resources']})")
    print(f"     Relationships: {len(future.twin['relationships'])}")

    for r_id, prov_list in future.provenance.items():
        print(f"     -> Provenance on {r_id}: {len(prov_list)} change(s)")
        for item in prov_list:
            print(f"        - [{item['operation'].upper()}] {item['property_path']}: {item['before']} -> {item['after']}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_run_cli())

