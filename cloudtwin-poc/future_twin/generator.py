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

from future_twin.models import FutureTwin
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
        out_path: str | Path | None = None,
    ) -> FutureTwin:
        raw_twin = self._load_twin(current_twin)
        future_data = copy.deepcopy(raw_twin)
        change_records = self._resolve_changes(changes, raw_twin)

        provenance: dict[str, list[dict[str, Any]]] = {}
        applied_changes: list[NormalizedChange] = []
        sim_id = simulation_id or f"sim-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        for change in change_records:
            self.mutator.apply_change(future_data, change, provenance)
            applied_changes.append(change)

        if "summary" in future_data and isinstance(future_data["summary"], dict):
            resources = future_data.get("resources", [])
            future_data["summary"]["total_resources"] = len(resources)
            future_data["summary"]["total_relationships"] = len(future_data.get("relationships", []))

        result = FutureTwin(
            twin=future_data,
            provenance=provenance,
            changes_applied=applied_changes,
            simulation_id=sim_id,
        )

        if out_path is not None:
            p = Path(out_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(result.to_dict(strict_schema=True), indent=2), encoding="utf-8")

        return result

    @staticmethod
    def _load_twin(twin: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(twin, dict):
            return twin
        if isinstance(twin, Path):
            return json.loads(twin.read_text(encoding="utf-8"))
        if isinstance(twin, str):
            p = Path(twin)
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
            return json.loads(twin)
        raise TypeError(f"Unsupported Current Twin type: {type(twin)}")

    @staticmethod
    def _resolve_changes(
        changes: Sequence[NormalizedChange] | ChangeSet | dict[str, Any] | str | Path,
        current_twin: dict[str, Any],
    ) -> list[NormalizedChange]:
        if isinstance(changes, ChangeSet):
            return list(changes.changes)
        if isinstance(changes, (list, tuple)):
            return list(changes)
        ingester = ChangeIngester(current_twin=current_twin)
        return list(ingester.ingest(changes).changes)
