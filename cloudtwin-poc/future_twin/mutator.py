"""Resource mutator for applying normalized changes to a Twin snapshot copy."""

from __future__ import annotations

import copy
from typing import Any

from future_twin.models import (
    DuplicateResourceError,
    InvalidMutationError,
    PropertyMutationError,
    TargetNotFoundError,
)
from ingestion.models import NormalizedChange, OperationType


class ResourceMutator:
    """Applies create, update, delete, and replace operations to a Twin dict."""

    def __init__(self, strict_conflicts: bool = False) -> None:
        self.strict_conflicts = strict_conflicts

    def apply_change(
        self,
        twin: dict[str, Any],
        change: NormalizedChange,
        provenance: dict[str, list[dict[str, Any]]],
    ) -> None:
        op = change.operation
        if op == OperationType.CREATE:
            self._apply_create(twin, change)
        elif op == OperationType.UPDATE:
            self._apply_update(twin, change)
        elif op == OperationType.DELETE:
            self._apply_delete(twin, change)
        elif op == OperationType.REPLACE:
            self._apply_replace(twin, change)
        else:
            raise InvalidMutationError(f"Unsupported operation: {op}")

        provenance.setdefault(change.resource_id, []).append({
            "change_id": change.change_id,
            "operation": op.value,
            "property_path": change.property_path,
            "before": copy.deepcopy(change.before),
            "after": copy.deepcopy(change.after),
            "source": change.source.to_dict() if hasattr(change.source, "to_dict") else change.source,
        })

    def _find_resource(self, twin: dict[str, Any], resource_id: str) -> tuple[int, dict[str, Any]] | None:
        for idx, res in enumerate(twin.get("resources", [])):
            if res.get("id") == resource_id:
                return idx, res
        return None

    def _apply_create(self, twin: dict[str, Any], change: NormalizedChange) -> None:
        if self._find_resource(twin, change.resource_id) is not None:
            raise DuplicateResourceError(f"Resource '{change.resource_id}' already exists.")

        new_res: dict[str, Any] = {
            "id": change.resource_id,
            "name": change.resource_id.split(":")[-1],
            "type": change.resource_type,
            "provider": change.provider,
            "properties": copy.deepcopy(change.after) if isinstance(change.after, dict) else {"value": copy.deepcopy(change.after)},
        }
        if change.namespace:
            new_res["namespace"] = change.namespace
        twin.setdefault("resources", []).append(new_res)

    def _apply_update(self, twin: dict[str, Any], change: NormalizedChange) -> None:
        found = self._find_resource(twin, change.resource_id)
        if found is None:
            raise TargetNotFoundError(f"Target resource '{change.resource_id}' not found for update.")
        _, res = found
        self._set_property_path(res, change.property_path, copy.deepcopy(change.after))

    def _apply_delete(self, twin: dict[str, Any], change: NormalizedChange) -> None:
        found = self._find_resource(twin, change.resource_id)
        if found is None:
            raise TargetNotFoundError(f"Target resource '{change.resource_id}' not found for delete.")
        idx, _ = found
        twin["resources"].pop(idx)
        twin["relationships"] = [
            rel for rel in twin.get("relationships", [])
            if rel.get("source") != change.resource_id and rel.get("target") != change.resource_id
        ]

    def _apply_replace(self, twin: dict[str, Any], change: NormalizedChange) -> None:
        found = self._find_resource(twin, change.resource_id)
        if found is None:
            raise TargetNotFoundError(f"Target resource '{change.resource_id}' not found for replace.")
        _, res = found
        if change.property_path in ("", "properties", "*"):
            if isinstance(change.after, dict):
                res["properties"] = copy.deepcopy(change.after)
            else:
                res["properties"] = {"value": copy.deepcopy(change.after)}
        else:
            self._set_property_path(res, change.property_path, copy.deepcopy(change.after))

    @staticmethod
    def _set_property_path(res: dict[str, Any], path: str, value: Any) -> None:
        if not path:
            raise PropertyMutationError("Empty property path.")
        parts = path.split(".")
        curr: Any = res
        for part in parts[:-1]:
            if not isinstance(curr, dict):
                raise PropertyMutationError(f"Cannot traverse non-dict at '{part}' in '{path}'.")
            if part not in curr:
                curr[part] = {}
            curr = curr[part]
        if not isinstance(curr, dict):
            raise PropertyMutationError(f"Cannot set property '{parts[-1]}' on non-dict.")
        curr[parts[-1]] = value
