"""La chaîne complète du Milestone 1, en un appel :

    Current Twin + changement proposé (tfplan.json, manifest YAML ou scénario)
      → ingestion (Hamdi)  → Future Twin  → Diff  → analyse (Fatma) : findings, blast radius, score

Rien n'est appliqué à AWS : tout se passe sur des copies du jumeau.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analysis.engine import run
from ingestion import ChangeIngester
from integration.hamdi_fatma import context_from_changeset
from simulation.diff import diff_twins, removed_relationships
from simulation.future_twin import build_future_twin


def _load(source: Any) -> Any:
    if isinstance(source, (str, Path)) and Path(source).suffix == ".json":
        return json.loads(Path(source).read_text(encoding="utf-8"))
    return source


def simulate(current_twin: dict[str, Any] | str | Path, change_source: Any,
             kind: str | None = None) -> dict[str, Any]:
    twin = _load(current_twin)
    changeset = ChangeIngester(twin).ingest(change_source, kind=kind)
    future = build_future_twin(twin, changeset)
    context = context_from_changeset(changeset, twin)
    result = run(context)
    return {
        "simulation_id": context.simulation_id,
        "changes": changeset.to_dict(),
        "future_twin": future.twin,
        "simulation": {
            "changed": future.changed,
            "created": future.created,
            "deleted": future.deleted,
            "warnings": future.warnings,
        },
        "diff": diff_twins(twin, future.twin),
        "removed_relationships": removed_relationships(twin, future.twin),
        "analysis": result.model_dump(mode="json"),
        "_context": context,     # pour --explain / --fixes, retiré de la sortie JSON
        "_result": result,
    }
