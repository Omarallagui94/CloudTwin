"""Data models and exception hierarchy for Future Twin generation."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ingestion.models import NormalizedChange


class FutureTwinError(Exception):
    """Base exception for Future Twin generation errors."""


class InvalidMutationError(FutureTwinError):
    """Raised when a proposed mutation cannot be applied."""


class TargetNotFoundError(InvalidMutationError):
    """Raised when an update, delete, or replace mutation targets a non-existent resource."""


class DuplicateResourceError(InvalidMutationError):
    """Raised when a create mutation targets an existing resource ID."""


class PropertyMutationError(InvalidMutationError):
    """Raised when navigating or modifying an invalid property path."""


@dataclass
class FutureTwin:
    """Represents a virtual Future Digital Twin produced by applying proposed changes.

    Attributes:
        twin: The normalized infrastructure snapshot conforming to twin.schema.json.
        provenance: Mapping from resource_id to list of change provenance records.
        changes_applied: List of NormalizedChange records that produced this state.
        simulation_id: Unique simulation identifier.
        created_at: ISO-8601 timestamp of simulation creation.
        metadata: Extra simulation and lineage metadata.
    """

    twin: dict[str, Any]
    provenance: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    changes_applied: list[NormalizedChange] = field(default_factory=list)
    simulation_id: str = field(default_factory=lambda: f"sim-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        """Find and return a resource by canonical ID."""
        for res in self.twin.get("resources", []):
            if res.get("id") == resource_id:
                return res
        return None

    def get_provenance(self, resource_id: str) -> list[dict[str, Any]]:
        """Retrieve change provenance history for a resource."""
        return self.provenance.get(resource_id, [])

    def to_dict(self, strict_schema: bool = True) -> dict[str, Any]:
        """Convert to snapshot dictionary.

        Args:
            strict_schema: When True (default), returns pure Twin JSON conforming
                           strictly to twin.schema.json (without extra top-level fields).
                           When False, includes simulation envelope metadata.
        """
        snapshot = copy.deepcopy(self.twin)
        if not strict_schema:
            snapshot["simulation_id"] = self.simulation_id
            snapshot["simulation_created_at"] = self.created_at
            snapshot["provenance"] = self.provenance
            snapshot["changes_count"] = len(self.changes_applied)
        return snapshot

    def to_envelope(
        self,
        current_snapshot_id: str = "snapshot-current",
        future_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert to team integration envelope format matching project contract."""
        f_id = future_snapshot_id or f"snapshot-{self.simulation_id}"
        return {
            "schema_version": "1.0",
            "simulation_id": self.simulation_id,
            "current_snapshot_id": current_snapshot_id,
            "future_snapshot_id": f_id,
            "created_at": self.created_at,
            "twin": self.twin,
            "changes": [c.to_dict() for c in self.changes_applied],
            "provenance": self.provenance,
        }
