"""Pont TEMPORAIRE entre le PoC actuel et le moteur d'analyse.

Le dépôt contient aujourd'hui :
  - cloudtwin-poc/outputs/current_state.json   (sortie du normalizer)
  - un scÃ©nario temporaire au format PoC simple (pas encore de `changes` canoniques)

Ce module construit un AnalysisContext à partir de ces deux fichiers pour pouvoir
tester le moteur de bout en bout dès maintenant. Il sera REMPLACÉ par la sortie
officielle de Hamdi (Future Twin + Diff + ressources impactées, tâches H4 à H7).
"""

from __future__ import annotations

from typing import Any

from analysis.models import SCHEMA_VERSION, AffectedResource, AnalysisContext, Change, ChangeSource, Twin

# "Deployment" (scénario) -> "DEPLOYMENT" (normalizer)
SCENARIO_TYPES = {
    "deployment": "DEPLOYMENT",
    "statefulset": "STATEFUL_SET",
    "service": "SERVICE",
}


class ScenarioError(ValueError):
    pass


def _find_resource(twin: Twin, resource_type: str, name: str, namespace: str | None) -> str:
    matches = [
        r for r in twin.resources
        if r.type == resource_type and r.name == name
        and (namespace is None or r.properties.get("namespace") == namespace)
    ]
    if not matches:
        raise ScenarioError(f"{resource_type} {name!r} introuvable dans le Current Twin")
    if len(matches) > 1:
        raise ScenarioError(f"{resource_type} {name!r} ambigu : préciser 'namespace' dans le scénario")
    return matches[0].id


# TEMPORARY, NON-CANONICAL: this one-hop neighbour list exists only for Fatma's
# current PoC tests. It is not Hamdi's dependency/affected-resource output and is
# not a blast-radius calculation. Replace it when Hamdi's canonical output lands.
def _neighbours(twin: Twin, change_id: str, rid: str) -> list[AffectedResource]:
    """Voisins à distance 1 (approximation ; le vrai calcul est la tâche H7 de Hamdi)."""
    result = []
    for rel in twin.relationships:
        if rel.target == rid:
            other, direction = rel.source, "incoming"
        elif rel.source == rid:
            other, direction = rel.target, "outgoing"
        else:
            continue
        result.append(AffectedResource(
            change_id=change_id, source=rid, target=other, relationship=rel.type,
            direction=direction, distance=1, impact="direct", path=[rid, other],
        ))
    return result


def context_from_poc(current_state: dict[str, Any], scenario: dict[str, Any]) -> AnalysisContext:
    twin = Twin.model_validate(current_state)
    rtype = SCENARIO_TYPES.get(str(scenario.get("resource_type", "")).lower())
    if rtype is None:
        raise ScenarioError(f"resource_type non supporté : {scenario.get('resource_type')!r}")

    rid = _find_resource(twin, rtype, scenario["resource"], scenario.get("namespace"))
    change = scenario["change"]
    change_id = "change-001"

    return AnalysisContext(
        schema_version=SCHEMA_VERSION,
        simulation_id=f"sim-{scenario.get('scenario_id', 'adhoc')}",
        current_twin=twin,
        changes=[Change(
            change_id=change_id,
            operation=change.get("operation", "update"),
            resource_id=rid,
            resource_type=rtype,
            provider="kubernetes" if rid.startswith("k8s:") else "aws",
            property_path=f"properties.{change['property']}" if change.get("property") else None,
            before=change.get("before"),
            after=change.get("after"),
            source=ChangeSource(kind="scenario", reference=scenario.get("scenario_id")),
        )],
        affected_resources=_neighbours(twin, change_id, rid),
    )
