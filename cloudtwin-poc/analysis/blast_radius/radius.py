"""Blast radius : quelles ressources un changement peut-il toucher ?

Pour chaque ressource modifiée, on parcourt le jumeau en suivant ce qui
DÉPEND d'elle, jusqu'à MAX_DEPTH sauts :

    impact direct    = distance 1 (voisin immédiat qui dépend de la ressource)
    impact indirect  = distance 2 ou plus

Le sens de chaque relation est celui de RULE-RES-003 (DEPENDENCY_DIRECTION) :
« LB ROUTES_TO Service » veut dire que le LB dépend du Service ; si le Service
change, le LB est touché. Un seul tableau de sens pour les deux, donc ils ne
peuvent pas se contredire.

Sortie : une liste au format AffectedResource (analysis/models.py), le même que
celui attendu de la tâche H7 de Hamdi. Si le contexte contient déjà des
ressources impactées (sortie de Hamdi), elles sont utilisées telles quelles.
"""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel

from analysis.models import AffectedResource, AnalysisContext, Twin
from analysis.rules.resilience.res_003_deleted_dependency import (
    DEPENDENCY_DIRECTION,
    NEVER_DEPENDENT_TYPES,
)

MAX_DEPTH = 3


class BlastRadius(BaseModel):
    source: str                         # "computed" (ce module) ou "provided" (Hamdi)
    max_depth: int
    direct: list[str]
    indirect: list[str]
    unknown_relationship_types: list[str]
    affected: list[AffectedResource]


def _dependents_index(twin: Twin) -> tuple[dict[str, list[tuple[str, str, str]]], set[str]]:
    """{ressource: [(dépendant, type de relation, sens)]} + types de relation inconnus."""
    index: dict[str, list[tuple[str, str, str]]] = {}
    unknown: set[str] = set()
    types = {r.id: r.type for r in twin.resources}
    for rel in twin.relationships:
        direction = DEPENDENCY_DIRECTION.get(rel.type)
        if direction is None:
            unknown.add(rel.type)
            continue
        if direction == "source":          # la source dépend de la cible
            dependency, dependent, way = rel.target, rel.source, "incoming"
        elif direction == "target":        # la cible dépend de la source
            dependency, dependent, way = rel.source, rel.target, "outgoing"
        else:
            continue
        if types.get(dependent) in NEVER_DEPENDENT_TYPES:
            continue
        index.setdefault(dependency, []).append((dependent, rel.type, way))
    for deps in index.values():
        deps.sort()
    return index, unknown


def compute(ctx: AnalysisContext, max_depth: int = MAX_DEPTH) -> BlastRadius:
    if ctx.affected_resources:
        return _summarize("provided", max_depth, ctx.affected_resources, set())

    index, unknown = _dependents_index(ctx.current_twin)
    changed = {c.resource_id for c in ctx.changes}
    affected: list[AffectedResource] = []

    for change in sorted(ctx.changes, key=lambda c: c.change_id):
        start = change.resource_id
        seen = {start}
        queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
        while queue:
            node, path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue
            for dependent, rel_type, way in index.get(node, []):
                if dependent in seen:
                    continue
                seen.add(dependent)
                new_path = path + [dependent]
                distance = len(new_path) - 1
                if dependent not in changed:
                    affected.append(AffectedResource(
                        change_id=change.change_id, source=start, target=dependent,
                        relationship=rel_type, direction=way, distance=distance,
                        impact="direct" if distance == 1 else "indirect", path=new_path,
                        reason=f"dépend de {node} ({rel_type})",
                    ))
                queue.append((dependent, new_path))

    return _summarize("computed", max_depth, affected, unknown)


def _summarize(source: str, max_depth: int, affected: list[AffectedResource],
               unknown: set[str]) -> BlastRadius:
    # Une ressource touchée directement par un changement est « directe »,
    # même si un autre changement l'atteint aussi de plus loin.
    direct = {a.target for a in affected if a.impact == "direct"}
    indirect = {a.target for a in affected if a.impact == "indirect"} - direct
    return BlastRadius(
        source=source, max_depth=max_depth,
        direct=sorted(direct), indirect=sorted(indirect),
        unknown_relationship_types=sorted(unknown),
        affected=sorted(affected, key=lambda a: (a.change_id, a.distance, a.target)),
    )
