"""Temporary bridge from Hamdi ChangeSet to Fatma AnalysisContext.

This maps only normalized changes and a caller-supplied, valid Current Twin.
It deliberately does not create a Future Twin, State Diff, dependency output,
affected resources, blast radius, security findings, or risk scores. Replace
this bridge when Hamdi delivers the canonical Future Twin and Diff contracts.
"""

from __future__ import annotations

from typing import Any

from analysis.models import AnalysisContext, SCHEMA_VERSION, Change, Twin
from ingestion.models import ChangeSet


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
    missing = [change.resource_id for change in changeset if change.resource_id not in known_ids]
    if missing:
        raise ValueError(f"ChangeSet targets absent from Current Twin: {sorted(set(missing))}")

    sid = simulation_id or f"sim-{changeset.metadata.get('scenario_id', 'local')}"
    return AnalysisContext(
        schema_version=SCHEMA_VERSION,
        simulation_id=sid,
        current_twin=twin,
        future_twin=None,
        changes=[Change.model_validate(change.to_dict()) for change in changeset],
        affected_resources=[],
    )
