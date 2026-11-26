"""Deterministic structural comparison of Current Twin and Future Twin."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _relationship_key(relationship: dict[str, Any]) -> tuple[str, str, str]:
    return relationship["source"], relationship["target"], relationship["type"]


def _property_changes(before: Any, after: Any, path: str = "properties") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}"
            if key not in before:
                changes.append({"property": child_path, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"property": child_path, "before": before[key], "after": None})
            else:
                changes.extend(_property_changes(before[key], after[key], child_path))
        return changes
    if before != after:
        return [{"property": path, "before": before, "after": after}]
    return []


@dataclass(frozen=True)
class StateDiff:
    """Resource diff plus relationship changes absent from diff.schema.json v1."""

    changes: list[dict[str, Any]] = field(default_factory=list)
    added_relationships: list[dict[str, str]] = field(default_factory=list)
    removed_relationships: list[dict[str, str]] = field(default_factory=list)
    schema_version: str = "1.0.0"

    @property
    def added_resources(self) -> list[dict[str, Any]]:
        return [change for change in self.changes if change["change_type"] == "ADDED"]

    @property
    def removed_resources(self) -> list[dict[str, Any]]:
        return [change for change in self.changes if change["change_type"] == "REMOVED"]

    @property
    def modified_resources(self) -> list[dict[str, Any]]:
        return [change for change in self.changes if change["change_type"] == "MODIFIED"]

    def canonical_dict(self) -> dict[str, Any]:
        """Return exactly the existing v1 diff.schema.json contract."""
        return {"schema_version": self.schema_version, "changes": self.changes}

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical diff plus explicit structural relationship changes."""
        return {
            **self.canonical_dict(),
            "added_relationships": self.added_relationships,
            "removed_relationships": self.removed_relationships,
        }


def calculate_state_diff(current_twin: dict[str, Any], future_twin: dict[str, Any]) -> StateDiff:
    """Compare two snapshots without deriving impact, risk, or dependencies."""
    current = {resource["id"]: resource for resource in current_twin.get("resources", [])}
    future = {resource["id"]: resource for resource in future_twin.get("resources", [])}
    changes: list[dict[str, Any]] = []

    for resource_id in sorted(future.keys() - current.keys()):
        changes.append({"resource_id": resource_id, "change_type": "ADDED", "after": future[resource_id]})
    for resource_id in sorted(current.keys() - future.keys()):
        changes.append({"resource_id": resource_id, "change_type": "REMOVED", "before": current[resource_id]})
    for resource_id in sorted(current.keys() & future.keys()):
        before_resource = current[resource_id]
        after_resource = future[resource_id]
        for property_change in _property_changes(
            before_resource.get("properties", {}), after_resource.get("properties", {})
        ):
            changes.append({
                "resource_id": resource_id,
                "change_type": "MODIFIED",
                **property_change,
            })

    current_relationships = {_relationship_key(item): item for item in current_twin.get("relationships", [])}
    future_relationships = {_relationship_key(item): item for item in future_twin.get("relationships", [])}
    added_relationships = [future_relationships[key] for key in sorted(future_relationships.keys() - current_relationships.keys())]
    removed_relationships = [current_relationships[key] for key in sorted(current_relationships.keys() - future_relationships.keys())]
    return StateDiff(
        changes=changes,
        added_relationships=added_relationships,
        removed_relationships=removed_relationships,
    )
