"""Property mutation and resource manipulation utilities for Future Twin generation."""

from __future__ import annotations

import copy
import re
from typing import Any

from future_twin.models import (
    DuplicateResourceError,
    PropertyMutationError,
    TargetNotFoundError,
)
from ingestion.models import ConflictError, NormalizedChange, OperationType

_INDEX_REGEX = re.compile(r"^([^\[\]]+)\[(\d+)\]$")


def get_nested_property(obj: Any, path: str) -> Any:
    """Retrieve a nested property value by dot-delimited path (with array indexing support).

    Examples:
        'properties.replicas'
        'properties.tags.Name'
        'properties.containers[0].image'
    """
    if not path or path == "properties":
        return obj.get("properties", {}) if isinstance(obj, dict) else obj

    segments = path.split(".")
    curr = obj

    for seg in segments:
        if curr is None:
            return None

        # Check for array indexing like containers[0]
        match = _INDEX_REGEX.match(seg)
        if match:
            attr_name, idx_str = match.groups()
            idx = int(idx_str)
            if not isinstance(curr, dict) or attr_name not in curr:
                return None
            seq = curr[attr_name]
            if not isinstance(seq, (list, tuple)) or idx >= len(seq):
                return None
            curr = seq[idx]
        else:
            if not isinstance(curr, dict) or seg not in curr:
                return None
            curr = curr[seg]

    return curr


def set_nested_property(obj: dict[str, Any], path: str, value: Any) -> None:
    """Set a nested property value by dot-delimited path (supporting auto-creating dicts).

    Raises:
        PropertyMutationError if path traversal cannot be performed.
    """
    if not path or path == "properties":
        if isinstance(value, dict):
            obj["properties"] = value
        else:
            obj["properties"] = {"value": value}
        return

    segments = path.split(".")
    curr: Any = obj

    for i, seg in enumerate(segments[:-1]):
        match = _INDEX_REGEX.match(seg)
        if match:
            attr_name, idx_str = match.groups()
            idx = int(idx_str)
            if attr_name not in curr or not isinstance(curr[attr_name], list):
                raise PropertyMutationError(f"Cannot navigate list at property segment '{seg}'")
            seq = curr[attr_name]
            if idx >= len(seq):
                raise PropertyMutationError(f"Index {idx} out of range in list '{attr_name}'")
            curr = seq[idx]
        else:
            if seg not in curr or not isinstance(curr[seg], dict):
                curr[seg] = {}
            curr = curr[seg]

    final_seg = segments[-1]
    match = _INDEX_REGEX.match(final_seg)
    if match:
        attr_name, idx_str = match.groups()
        idx = int(idx_str)
        if attr_name not in curr or not isinstance(curr[attr_name], list):
            raise PropertyMutationError(f"Cannot set list element at '{final_seg}'")
        seq = curr[attr_name]
        if idx >= len(seq):
            raise PropertyMutationError(f"Index {idx} out of range in list '{attr_name}'")
        seq[idx] = value
    else:
        if not isinstance(curr, dict):
            raise PropertyMutationError(f"Cannot set attribute '{final_seg}' on non-dict parent")
        curr[final_seg] = value


class ResourceMutator:
    """Applies individual NormalizedChange mutations to a mutable Twin dictionary."""

    def __init__(self, strict_conflicts: bool = False) -> None:
        self.strict_conflicts = strict_conflicts

    def apply_change(
        self,
        twin: dict[str, Any],
        change: NormalizedChange,
        provenance: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Apply a normalized change to the given twin dictionary and record provenance."""
        op = change.operation

        if op == OperationType.CREATE:
            self._apply_create(twin, change, provenance)
        elif op == OperationType.UPDATE:
            self._apply_update(twin, change, provenance)
        elif op == OperationType.DELETE:
            self._apply_delete(twin, change, provenance)
        elif op == OperationType.REPLACE:
            self._apply_replace(twin, change, provenance)
        else:
            raise ValueError(f"Unsupported operation type: {op}")

    def _apply_create(
        self,
        twin: dict[str, Any],
        change: NormalizedChange,
        provenance: dict[str, list[dict[str, Any]]],
    ) -> None:
        r_id = change.resource_id
        resources = twin.setdefault("resources", [])

        # Validate no duplicate ID
        if any(r.get("id") == r_id for r in resources):
            raise DuplicateResourceError(f"Cannot create resource '{r_id}': ID already exists in Twin.")

        # Determine display name
        name = r_id.split(":")[-1]
        if isinstance(change.after, dict):
            if "name" in change.after and change.after["name"]:
                name = str(change.after["name"])
            elif "tags" in change.after and isinstance(change.after["tags"], dict):
                name = change.after["tags"].get("Name", name)

        properties = change.after if isinstance(change.after, dict) else {"value": change.after}

        new_resource = {
            "id": r_id,
            "name": name,
            "type": change.resource_type,
            "provider": change.provider,
            "properties": properties,
        }
        resources.append(new_resource)

        self._record_provenance(provenance, r_id, change)

    def _apply_update(
        self,
        twin: dict[str, Any],
        change: NormalizedChange,
        provenance: dict[str, list[dict[str, Any]]],
    ) -> None:
        r_id = change.resource_id
        res = self._find_resource(twin, r_id)
        if not res:
            raise TargetNotFoundError(f"Target resource '{r_id}' not found for update mutation.")

        prop_path = change.property_path or "properties"

        # Conflict check if strict mode is active
        if self.strict_conflicts and change.before is not None:
            current_val = get_nested_property(res, prop_path)
            if current_val is not None and current_val != change.before:
                raise ConflictError(
                    f"Conflict on {r_id} at {prop_path}: expected '{change.before}', current is '{current_val}'."
                )

        set_nested_property(res, prop_path, change.after)
        self._record_provenance(provenance, r_id, change)

    def _apply_delete(
        self,
        twin: dict[str, Any],
        change: NormalizedChange,
        provenance: dict[str, list[dict[str, Any]]],
    ) -> None:
        r_id = change.resource_id
        res = self._find_resource(twin, r_id)
        if not res:
            raise TargetNotFoundError(f"Target resource '{r_id}' not found for delete mutation.")

        # Remove resource
        twin["resources"] = [r for r in twin.get("resources", []) if r.get("id") != r_id]

        # Remove connecting relationships to prevent dangling edges
        twin["relationships"] = [
            rel for rel in twin.get("relationships", [])
            if rel.get("source") != r_id and rel.get("target") != r_id
        ]

        self._record_provenance(provenance, r_id, change)

    def _apply_replace(
        self,
        twin: dict[str, Any],
        change: NormalizedChange,
        provenance: dict[str, list[dict[str, Any]]],
    ) -> None:
        r_id = change.resource_id
        res = self._find_resource(twin, r_id)
        if not res:
            raise TargetNotFoundError(f"Target resource '{r_id}' not found for replace mutation.")

        prop_path = change.property_path or "properties"
        if prop_path == "properties" and isinstance(change.after, dict):
            # Preserve name/type/provider while replacing properties
            res["properties"] = copy.deepcopy(change.after)
        else:
            set_nested_property(res, prop_path, change.after)

        self._record_provenance(provenance, r_id, change)

    @staticmethod
    def _find_resource(twin: dict[str, Any], resource_id: str) -> dict[str, Any] | None:
        for r in twin.get("resources", []):
            if r.get("id") == resource_id:
                return r
        return None

    @staticmethod
    def _record_provenance(
        provenance: dict[str, list[dict[str, Any]]],
        resource_id: str,
        change: NormalizedChange,
    ) -> None:
        record = {
            "change_id": change.change_id,
            "operation": change.operation.value,
            "property_path": change.property_path,
            "before": change.before,
            "after": change.after,
            "source": change.source,
        }
        provenance.setdefault(resource_id, []).append(record)
