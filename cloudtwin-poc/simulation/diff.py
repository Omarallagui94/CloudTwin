"""Diff entre le Current Twin et le Future Twin, au format schema/v1/diff.schema.json.

Une entrée par ressource ajoutée (ADDED) ou retirée (REMOVED), et une entrée
par propriété modifiée (MODIFIED), avec sa valeur avant et après. Les
dictionnaires sont comparés clé par clé (`properties.a.b`) ; les listes sont
comparées en entier (une règle d'ingress modifiée = toute la liste).

Les relations retirées avec une ressource supprimée sont listées à part
(`removed_relationships`) : le schéma diff v1 ne décrit que les ressources.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0.0"


def _resources(twin: Any) -> dict[str, dict[str, Any]]:
    data = twin.model_dump(mode="json") if hasattr(twin, "model_dump") else twin
    return {r["id"]: r for r in data.get("resources", [])}


def _relationships(twin: Any) -> set[tuple[str, str, str]]:
    data = twin.model_dump(mode="json") if hasattr(twin, "model_dump") else twin
    return {(r["source"], r["type"], r["target"]) for r in data.get("relationships", [])}


def _leaves(before: Any, after: Any, path: str) -> list[tuple[str, Any, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for key in sorted(set(before) | set(after)):
            out += _leaves(before.get(key), after.get(key), f"{path}.{key}")
        return out
    return [] if before == after else [(path, before, after)]


def diff_twins(current: Any, future: Any) -> dict[str, Any]:
    old, new = _resources(current), _resources(future)
    changes: list[dict[str, Any]] = []
    for rid in sorted(set(new) - set(old)):
        changes.append({"resource_id": rid, "change_type": "ADDED", "after": new[rid]})
    for rid in sorted(set(old) - set(new)):
        changes.append({"resource_id": rid, "change_type": "REMOVED", "before": old[rid]})
    for rid in sorted(set(old) & set(new)):
        for field in ("name", "type", "provider"):
            if old[rid].get(field) != new[rid].get(field):
                changes.append({"resource_id": rid, "change_type": "MODIFIED", "property": field,
                                "before": old[rid].get(field), "after": new[rid].get(field)})
        for prop, before, after in _leaves(old[rid].get("properties", {}),
                                           new[rid].get("properties", {}), "properties"):
            changes.append({"resource_id": rid, "change_type": "MODIFIED", "property": prop,
                            "before": before, "after": after})
    return {"schema_version": SCHEMA_VERSION, "changes": changes}


def removed_relationships(current: Any, future: Any) -> list[dict[str, str]]:
    return [{"source": s, "type": t, "target": d}
            for s, t, d in sorted(_relationships(current) - _relationships(future))]


def changed_resource_ids(diff: dict[str, Any]) -> list[str]:
    return sorted({c["resource_id"] for c in diff["changes"]})
