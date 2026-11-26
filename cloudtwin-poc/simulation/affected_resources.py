"""Deterministic structural affected-resource analysis.

This module identifies structural impact only. It never assigns severity,
risk, remediation, or security meaning.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from pydantic import BaseModel, Field

from analysis.models import AffectedResource, Change, Twin


# ``source`` means the relationship source depends on its target; ``target``
# means its target depends on its source; ``none`` is known but non-propagating.
PROPAGATION_DIRECTION: dict[str, str] = {
    "CONTAINS": "target",
    "DEPENDS_ON": "source",
    "EXPOSES": "source",
    "LOCATED_IN": "source",
    "MEMBER_OF": "source",
    "ROUTES_TO": "source",
    "RUNS_ON": "source",
    "USES": "source",
    "SELECTS": "none",
}

NEVER_DEPENDENT_TYPES = {"INTERNET"}


class AffectedResourcesResult(BaseModel):
    affected: list[AffectedResource] = Field(default_factory=list)
    unknown_relationship_types: list[str] = Field(default_factory=list)
    data_complete: bool = True


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _changes(values: Iterable[Any]) -> list[Change]:
    return [value if isinstance(value, Change) else Change.model_validate(_as_dict(value)) for value in values]


def _dependency_index(twin: Twin) -> tuple[dict[str, list[tuple[str, str, str]]], set[str]]:
    """Return dependency -> sorted dependents and unknown relationship types."""
    index: dict[str, list[tuple[str, str, str]]] = {}
    unknown: set[str] = set()
    resource_types = {resource.id: resource.type for resource in twin.resources}
    for relationship in twin.relationships:
        semantics = PROPAGATION_DIRECTION.get(relationship.type)
        if semantics is None:
            unknown.add(relationship.type)
            continue
        if semantics == "none":
            continue
        if semantics == "source":
            dependency, dependent, direction = relationship.target, relationship.source, "incoming"
        else:
            dependency, dependent, direction = relationship.source, relationship.target, "outgoing"
        if resource_types.get(dependent) in NEVER_DEPENDENT_TYPES:
            continue
        index.setdefault(dependency, []).append((dependent, relationship.type, direction))
    for dependents in index.values():
        dependents.sort(key=lambda item: (item[0], item[1], item[2]))
    return index, unknown


def _self_impact(change: Change, twin: Twin, complete: bool) -> AffectedResource:
    resource = twin.get(change.resource_id)
    return AffectedResource(
        change_id=change.change_id,
        source=change.resource_id,
        target=change.resource_id,
        resource_id=change.resource_id,
        resource_name=resource.name if resource else None,
        relationship="SELF",
        direction="outgoing",
        distance=0,
        impact="direct",
        path=[change.resource_id],
        relationship_types=[],
        reason=f"{change.resource_id} is the resource changed by {change.change_id}.",
        confidence="high" if complete else "medium",
        data_complete=complete,
    )


def analyze_affected_resources(
    state_diff: dict[str, Any],
    current_twin: dict[str, Any] | Twin,
    future_twin: dict[str, Any] | Twin,
    changes: Iterable[Any],
) -> AffectedResourcesResult:
    """Return shortest deterministic paths justified by dependency semantics.

    Updates and creates include the changed resource only. Dependency traversal
    applies to deletions using Current Twin edges, including edges absent from
    the Future Twin. This keeps replica changes local while preserving deleted
    dependency evidence such as S05.
    """
    current = current_twin if isinstance(current_twin, Twin) else Twin.model_validate(current_twin)
    future = future_twin if isinstance(future_twin, Twin) else Twin.model_validate(future_twin)
    normalized_changes = sorted(_changes(changes), key=lambda item: item.change_id)
    diff_ids = {entry["resource_id"] for entry in state_diff.get("changes", [])}
    current_index, current_unknown = _dependency_index(current)
    _, future_unknown = _dependency_index(future)
    unknown = current_unknown | future_unknown
    complete = not unknown
    affected: list[AffectedResource] = []

    for change in normalized_changes:
        if change.resource_id not in diff_ids:
            continue
        affected.append(_self_impact(change, current if change.operation == "delete" else future, complete))
        if change.operation != "delete":
            continue

        seen = {change.resource_id}
        queue: deque[tuple[str, list[str], list[str]]] = deque(
            [(change.resource_id, [change.resource_id], [])]
        )
        while queue:
            dependency, path, relationship_types = queue.popleft()
            for dependent, relationship_type, direction in current_index.get(dependency, []):
                if dependent in seen:
                    continue
                seen.add(dependent)
                if direction == "incoming":
                    next_path = [dependent] + path
                    next_types = [relationship_type] + relationship_types
                else:
                    next_path = path + [dependent]
                    next_types = relationship_types + [relationship_type]
                resource = current.get(dependent) or future.get(dependent)
                distance = len(next_types)
                affected.append(AffectedResource(
                    change_id=change.change_id,
                    source=change.resource_id,
                    target=dependent,
                    resource_id=dependent,
                    resource_name=resource.name if resource else None,
                    relationship=relationship_type,
                    direction=direction,
                    distance=distance,
                    impact="direct" if distance == 1 else "indirect",
                    path=next_path,
                    relationship_types=next_types,
                    reason=(f"{resource.name if resource else dependent} depends on the removed "
                            f"resource through {' -> '.join(next_types)}."),
                    confidence="high" if complete else "medium",
                    data_complete=complete,
                ))
                queue.append((dependent, next_path, next_types))

    affected.sort(key=lambda item: (item.change_id, item.distance, item.target, item.path))
    return AffectedResourcesResult(
        affected=affected,
        unknown_relationship_types=sorted(unknown),
        data_complete=complete,
    )
