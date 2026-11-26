"""State Diff engine for comparing Current Twin and Future Twin snapshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from diff.models import DiffChangeType, ResourceDiffRecord, StateDiff


class StateDiffer:
    """Computes exact, machine-readable differences between Current and Future twins."""

    def __init__(self, property_prefix: str = "properties") -> None:
        self.property_prefix = property_prefix

    def diff(
        self,
        current_twin: dict[str, Any] | str | Path,
        future_twin: Any,
    ) -> StateDiff:
        """Calculate state differences between Current Twin and Future Twin."""
        curr_data = self._load_twin(current_twin)
        fut_data, provenance = self._extract_twin_and_provenance(future_twin)

        curr_resources = {r["id"]: r for r in curr_data.get("resources", []) if "id" in r}
        fut_resources = {r["id"]: r for r in fut_data.get("resources", []) if "id" in r}

        changes: list[ResourceDiffRecord] = []
        all_ids = sorted(set(curr_resources.keys()).union(fut_resources.keys()))

        for r_id in all_ids:
            in_curr = r_id in curr_resources
            in_fut = r_id in fut_resources

            if not in_curr and in_fut:
                fut_res = fut_resources[r_id]
                changes.append(
                    ResourceDiffRecord(
                        resource_id=r_id,
                        change_type=DiffChangeType.ADDED,
                        property=self.property_prefix,
                        before=None,
                        after=fut_res.get("properties"),
                    )
                )
                continue

            if in_curr and not in_fut:
                curr_res = curr_resources[r_id]
                changes.append(
                    ResourceDiffRecord(
                        resource_id=r_id,
                        change_type=DiffChangeType.REMOVED,
                        property=self.property_prefix,
                        before=curr_res.get("properties"),
                        after=None,
                    )
                )
                continue

            curr_res = curr_resources[r_id]
            fut_res = fut_resources[r_id]

            r_prov = provenance.get(r_id, [])
            is_replaced = any(p.get("operation") in ("replace", "REPLACE") for p in r_prov)

            if is_replaced:
                changes.append(
                    ResourceDiffRecord(
                        resource_id=r_id,
                        change_type=DiffChangeType.REPLACED,
                        property=self.property_prefix,
                        before=curr_res.get("properties"),
                        after=fut_res.get("properties"),
                    )
                )
                continue

            prop_diffs = self._diff_properties(
                curr_res.get("properties", {}),
                fut_res.get("properties", {}),
                base_path=self.property_prefix,
            )

            for path_str, before_val, after_val in prop_diffs:
                changes.append(
                    ResourceDiffRecord(
                        resource_id=r_id,
                        change_type=DiffChangeType.MODIFIED,
                        property=path_str,
                        before=before_val,
                        after=after_val,
                    )
                )

            if curr_res.get("name") != fut_res.get("name"):
                changes.append(
                    ResourceDiffRecord(
                        resource_id=r_id,
                        change_type=DiffChangeType.MODIFIED,
                        property="name",
                        before=curr_res.get("name"),
                        after=fut_res.get("name"),
                    )
                )

        summary = {
            "total_changes": len(changes),
            "added": sum(1 for c in changes if c.change_type == DiffChangeType.ADDED),
            "removed": sum(1 for c in changes if c.change_type == DiffChangeType.REMOVED),
            "modified": sum(1 for c in changes if c.change_type == DiffChangeType.MODIFIED),
            "replaced": sum(1 for c in changes if c.change_type == DiffChangeType.REPLACED),
        }

        return StateDiff(
            schema_version="1.0.0",
            changes=changes,
            summary=summary,
        )

    def _diff_properties(
        self,
        before: Any,
        after: Any,
        base_path: str,
    ) -> list[tuple[str, Any, Any]]:
        diffs: list[tuple[str, Any, Any]] = []

        if isinstance(before, dict) and isinstance(after, dict):
            all_keys = sorted(set(before.keys()).union(after.keys()))
            for k in all_keys:
                current_path = f"{base_path}.{k}"
                if k not in before:
                    diffs.append((current_path, None, after[k]))
                elif k not in after:
                    diffs.append((current_path, before[k], None))
                elif before[k] != after[k]:
                    if isinstance(before[k], dict) and isinstance(after[k], dict):
                        diffs.extend(self._diff_properties(before[k], after[k], current_path))
                    else:
                        diffs.append((current_path, before[k], after[k]))
        elif before != after:
            diffs.append((base_path, before, after))

        return diffs

    @staticmethod
    def _extract_twin_and_provenance(future_input: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        if hasattr(future_input, "twin") and isinstance(future_input.twin, dict):
            prov = getattr(future_input, "provenance", {}) or {}
            return future_input.twin, prov

        twin_data = StateDiffer._load_twin(future_input)
        if isinstance(twin_data, dict) and "twin" in twin_data and isinstance(twin_data["twin"], dict):
            prov = twin_data.get("provenance", {})
            return twin_data["twin"], prov

        prov = twin_data.get("provenance", {})
        return twin_data, prov

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
        raise TypeError(f"Unsupported Twin snapshot type: {type(twin)}")


def compute_diff(
    current_twin: dict[str, Any] | str | Path,
    future_twin: Any,
) -> StateDiff:
    """Convenience function to compute StateDiff between Current and Future twins."""
    return StateDiffer().diff(current_twin, future_twin)
