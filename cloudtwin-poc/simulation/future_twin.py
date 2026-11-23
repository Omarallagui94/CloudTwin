"""Future Twin : appliquer les changements proposés au jumeau, sans toucher à AWS.

    current_twin + changements (ingestion de Hamdi)  →  future_twin

- update  : la propriété visée (`properties.a.b`) prend la valeur `after`.
- replace : toutes les propriétés sont remplacées par `after`.
- create  : une nouvelle ressource est ajoutée (sans relation : le plan Terraform
            ne dit pas à quoi elle est reliée).
- delete  : la ressource et toutes ses relations sont retirées.

Le Future Twin garde exactement le format du jumeau d'entrée : même clés,
mêmes champs par ressource (le schéma twin v1 interdit tout champ en plus).
Ce qui a changé est donc décrit à côté : dans `changed` et dans le Diff.
Le jumeau d'entrée n'est jamais modifié.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

MISSING = object()

# Propriétés « combinées » des scénarios : elles se répartissent sur de vrais champs du jumeau.
COMPOSITE_PROPERTIES = {
    "properties.service_type_and_scheme": {"service_type": "service_type", "scheme": "scheme"},
}


@dataclass
class FutureTwin:
    twin: dict[str, Any]                                         # même format que le Current Twin
    changed: dict[str, list[str]] = field(default_factory=dict)  # ressource -> propriétés modifiées
    created: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _get(data: Any, path: list[str]) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return MISSING
        data = data[key]
    return data


def _set(data: dict[str, Any], path: list[str], value: Any) -> None:
    for key in path[:-1]:
        if not isinstance(data.get(key), dict):
            data[key] = {}
        data = data[key]
    data[path[-1]] = value


def _as_dict(change: Any) -> dict[str, Any]:
    if isinstance(change, dict):
        return change
    if hasattr(change, "model_dump"):
        return change.model_dump(mode="json")
    if hasattr(change, "to_dict"):
        return change.to_dict()
    raise TypeError(f"changement illisible : {type(change).__name__}")


def _summary(twin: dict[str, Any]) -> dict[str, int]:
    resources = twin.get("resources", [])
    return {
        "aws_resources": sum(1 for r in resources if r.get("provider") == "aws"),
        "kubernetes_resources": sum(1 for r in resources if r.get("provider") == "kubernetes"),
        "relationships": len(twin.get("relationships", [])),
    }


def build_future_twin(current_twin: Any, changes: Iterable[Any]) -> FutureTwin:
    current = current_twin.model_dump(mode="json", exclude_none=True) \
        if hasattr(current_twin, "model_dump") else current_twin
    twin = copy.deepcopy(current)
    resources: dict[str, dict[str, Any]] = {r["id"]: r for r in twin.get("resources", [])}
    order = [r["id"] for r in twin.get("resources", [])]
    result = FutureTwin(twin=twin)

    for raw in sorted((_as_dict(c) for c in changes), key=lambda c: c["change_id"]):
        cid, op, rid = raw["change_id"], raw["operation"], raw["resource_id"]
        path = raw.get("property_path")
        after = raw.get("after")

        if op == "delete":
            if resources.pop(rid, None) is None:
                result.warnings.append(f"{cid} : {rid} à supprimer n'existe pas dans le jumeau")
            else:
                result.deleted.append(rid)
            continue

        if op == "create":
            if rid in resources:
                result.warnings.append(f"{cid} : {rid} existe déjà, la création est traitée comme un remplacement")
            props = after if isinstance(after, dict) else {}
            name = str(props.get("name") or rid.rsplit(":", 1)[-1] or rid)
            resources[rid] = {"id": rid, "name": name, "type": raw["resource_type"],
                              "provider": raw["provider"], "properties": copy.deepcopy(props)}
            if rid not in order:
                order.append(rid)
            result.created.append(rid)
            continue

        resource = resources.get(rid)
        if resource is None:
            result.warnings.append(f"{cid} : {rid} à modifier n'existe pas dans le jumeau")
            continue

        if op == "replace" or path in (None, "properties"):
            resource["properties"] = copy.deepcopy(after) if isinstance(after, dict) else {}
            result.changed.setdefault(rid, []).append("properties")
            continue

        if path in COMPOSITE_PROPERTIES and isinstance(after, dict):
            for src, dst in COMPOSITE_PROPERTIES[path].items():
                if src in after:
                    resource.setdefault("properties", {})[dst] = copy.deepcopy(after[src])
                    result.changed.setdefault(rid, []).append(f"properties.{dst}")
            continue

        keys = path.split(".")
        if keys[0] != "properties" or len(keys) < 2:
            result.warnings.append(f"{cid} : chemin {path!r} non pris en charge")
            continue
        current_value = _get(resource, keys)
        before = raw.get("before")
        if current_value is not MISSING and before is not None and current_value != before:
            result.warnings.append(
                f"{cid} : {rid} {path} vaut {current_value!r} dans le jumeau, le changement attendait {before!r}")
        _set(resource, keys, copy.deepcopy(after))
        result.changed.setdefault(rid, []).append(path)

    deleted = set(result.deleted)
    twin["resources"] = [resources[rid] for rid in order if rid in resources]
    twin["relationships"] = [rel for rel in twin.get("relationships", [])
                             if rel["source"] not in deleted and rel["target"] not in deleted]
    if "summary" in twin:
        twin["summary"] = _summary(twin)
    return result
