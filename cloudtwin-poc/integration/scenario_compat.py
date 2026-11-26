"""TEMPORARY compatibility helpers for Omar's local scenario fixtures.

These helpers exist only until Hamdi provides all of the following canonical
outputs: the Change schema, Future Twin, State Diff, and affected-resource /
dependency output. They translate fixture metadata only; they do not implement,
infer, or fabricate any of those outputs. Fatma's analysis engine and legacy PoC
adapter remain unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CompatibilityError(ValueError):
    """Raised when a scenario cannot be represented by the temporary adapter."""


_ID_KIND_TO_RESOURCE_TYPE = {
    "deployment": "DEPLOYMENT",
    "statefulset": "STATEFUL_SET",
    "service": "SERVICE",
}
_REQUIRED_FIELDS = (
    "scenario_id",
    "name",
    "target_resource",
    "resource_type",
    "operation",
    "property",
    "before",
    "after",
)


def discover_scenarios(scenarios_root: str | Path) -> dict[str, dict[str, Any]]:
    """Load scenario.json files from the current directory-based scenario pack."""
    root = Path(scenarios_root)
    discovered: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*/scenario.json")):
        try:
            scenario = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CompatibilityError(f"Cannot load scenario file {path}: {exc}") from exc

        scenario_id = scenario.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise CompatibilityError(f"Scenario file {path} has no valid scenario_id")
        if scenario_id in discovered:
            raise CompatibilityError(f"Duplicate scenario_id {scenario_id!r} in {path}")
        discovered[scenario_id] = scenario

    return dict(sorted(discovered.items()))


def to_fatma_poc_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Map one Omar fixture to Fatma's current legacy PoC adapter input.

    The canonical target ID is retained in the returned metadata even though the
    legacy adapter locates resources by name, type, and namespace. Unsupported or
    inconsistent targets fail rather than being guessed. This function must be
    removed when Hamdi's canonical contract is available, not expanded into a
    second Change, Future Twin, Diff, or dependency engine.
    """
    missing = [field for field in _REQUIRED_FIELDS if field not in scenario]
    if missing:
        raise CompatibilityError(f"Scenario is missing required field(s): {', '.join(missing)}")

    target_resource = scenario["target_resource"]
    if not isinstance(target_resource, str):
        raise CompatibilityError("target_resource must be a canonical CloudTwin resource ID")

    parts = target_resource.split(":", 3)
    if len(parts) != 4 or parts[0] != "k8s" or not all(parts[1:]):
        raise CompatibilityError(
            "Temporary compatibility supports canonical Kubernetes resource IDs "
            "in the form k8s:<kind>:<namespace>:<name>"
        )

    _, id_kind, namespace, resource_name = parts
    expected_type = _ID_KIND_TO_RESOURCE_TYPE.get(id_kind.lower())
    if expected_type is None:
        raise CompatibilityError(f"Unsupported Kubernetes resource kind in {target_resource!r}")

    resource_type = str(scenario["resource_type"]).upper()
    if resource_type != expected_type:
        raise CompatibilityError(
            f"resource_type {resource_type!r} does not match canonical ID kind {id_kind!r}"
        )

    operation = scenario["operation"]
    if operation not in {"create", "update", "delete", "replace"}:
        raise CompatibilityError(f"Unsupported scenario operation: {operation!r}")

    return {
        "scenario_id": scenario["scenario_id"],
        "name": scenario["name"],
        "description": scenario.get("description"),
        "target_resource": target_resource,
        "resource_type": resource_type,
        "resource": resource_name,
        "namespace": namespace,
        "change": {
            "operation": operation,
            "property": scenario["property"],
            "before": scenario["before"],
            "after": scenario["after"],
        },
    }
