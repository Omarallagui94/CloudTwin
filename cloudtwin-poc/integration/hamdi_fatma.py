"""Bridge from Hamdi ChangeSet to Fatma AnalysisContext.

Maps normalized changes and a caller-supplied Current Twin, and builds the
Future Twin with simulation.future_twin (the changes applied to a copy of the
twin, never to AWS). It also provides deterministic structural affected-resource
paths to Fatma's context. Findings, severity, remediation, and risk remain the
responsibility of the analysis engine.
"""

from __future__ import annotations

from typing import Any

from analysis.models import AnalysisContext, SCHEMA_VERSION, Change, Twin
from ingestion.models import ChangeSet
from simulation.affected_resources import analyze_affected_resources
from simulation.diff import diff_twins
from simulation.future_twin import build_future_twin


def context_from_changeset(
    changeset: ChangeSet,
    current_twin: dict[str, Any],
    *,
    future_twin: dict[str, Any] | None = None,
    state_diff: dict[str, Any] | None = None,
    simulation_id: str | None = None,
) -> AnalysisContext:
    """Build a Fatma context without inferring unavailable simulation data."""
    if changeset.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"ChangeSet schema {changeset.schema_version!r} does not match {SCHEMA_VERSION!r}"
        )
    twin = Twin.model_validate(current_twin)
    simulated_data = future_twin or build_future_twin(current_twin, changeset).twin
    simulated_twin = Twin.model_validate(simulated_data)
    known_ids = {resource.id for resource in twin.resources}
    # A created resource is, by definition, not in the Current Twin yet.
    missing = [change.resource_id for change in changeset
               if change.resource_id not in known_ids and change.operation.value != "create"]
    if missing:
        raise ValueError(f"ChangeSet targets absent from Current Twin: {sorted(set(missing))}")

    sid = simulation_id or f"sim-{changeset.metadata.get('scenario_id', 'local')}"
    changes = [Change.model_validate(change.to_dict()) for change in changeset]
    structural = analyze_affected_resources(
        state_diff or diff_twins(current_twin, simulated_data),
        twin,
        simulated_twin,
        changes,
    )
    return AnalysisContext(
        schema_version=SCHEMA_VERSION,
        simulation_id=sid,
        current_twin=twin,
        future_twin=simulated_twin,
        changes=changes,
        # The changed resource (distance 0) is exposed by the structural output,
        # but Fatma's context receives dependent resources only.
        affected_resources=[item for item in structural.affected if item.distance > 0],
    )
