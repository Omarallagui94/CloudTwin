"""Bridge from Hamdi ChangeSet to Fatma AnalysisContext.

Maps normalized changes and a caller-supplied Current Twin, and builds the
Future Twin with simulation.future_twin (the changes applied to a copy of the
twin, never to AWS). Affected resources, blast radius, findings and the risk
score are computed by the analysis engine, not here.
"""

from __future__ import annotations

from typing import Any

from analysis.models import AnalysisContext, SCHEMA_VERSION, Change, Twin
from ingestion.models import ChangeSet
from simulation.future_twin import build_future_twin


def context_from_changeset(
    changeset: ChangeSet,
    current_twin: dict[str, Any],
    *,
    simulation_id: str | None = None,
) -> AnalysisContext:
    """Build a Fatma context without inferring unavailable simulation data."""
    if changeset.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"ChangeSet schema {changeset.schema_version!r} does not match {SCHEMA_VERSION!r}"
        )
    twin = Twin.model_validate(current_twin)
    known_ids = {resource.id for resource in twin.resources}
    # A created resource is, by definition, not in the Current Twin yet.
    missing = [change.resource_id for change in changeset
               if change.resource_id not in known_ids and change.operation.value != "create"]
    if missing:
        raise ValueError(f"ChangeSet targets absent from Current Twin: {sorted(set(missing))}")

    sid = simulation_id or f"sim-{changeset.metadata.get('scenario_id', 'local')}"
    return AnalysisContext(
        schema_version=SCHEMA_VERSION,
        simulation_id=sid,
        current_twin=twin,
        future_twin=Twin.model_validate(build_future_twin(current_twin, changeset).twin),
        changes=[Change.model_validate(change.to_dict()) for change in changeset],
        affected_resources=[],
    )
