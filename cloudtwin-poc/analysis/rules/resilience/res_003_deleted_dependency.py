"""RULE-RES-003 — Suppression d'une ressource dont d'autres dépendent.

Logique :
  - on regarde les changements `delete` ;
  - on cherche dans le Current Twin les ressources qui DÉPENDENT de la
    ressource supprimée ;
  - FAIL           s'il reste au moins un dépendant qui n'est pas lui-même supprimé ;
  - PASS           si aucun dépendant ;
  - NOT_EVALUATED  si la ressource supprimée est absente du Current Twin.

Le sens de dépendance dépend du type de relation (voir DEPENDENCY_DIRECTION).
Les 8 types ci-dessous sont exactement ceux créés par
cloudtwin-poc/discovery/normalizer.py. ⚠️ Le sens doit être confirmé par Hamdi.
"""

from __future__ import annotations

from analysis.models import AnalysisContext, Category, Finding, Severity, Status
from analysis.rules.base import Rule

# "source" -> la source dépend de la cible   (LB ROUTES_TO Service : le LB dépend du Service)
# "target" -> la cible dépend de la source   (VPC CONTAINS Subnet : le Subnet dépend du VPC)
# "none"   -> relation connue, mais pas une dépendance de disponibilité
DEPENDENCY_DIRECTION: dict[str, str] = {
    "CONTAINS": "target",    # VPC→Subnet, EKS→NodeGroup, Namespace→Deployment
    "LOCATED_IN": "source",  # EC2/LB/EKS→Subnet ou VPC
    "USES": "source",        # EC2→SecurityGroup, Pod→ServiceAccount, EKS→IAMRole
    "MEMBER_OF": "source",   # EC2→NodeGroup
    "RUNS_ON": "source",     # Pod→Node, Node→EC2
    "EXPOSES": "source",     # Service→Deployment (Internet→LB : jamais supprimé)
    "ROUTES_TO": "source",   # LB→Service, Ingress→Service
    # Deployment→Pod, Service→Pod, NetworkPolicy→Pod. Les Pods sont recréés par
    # leur contrôleur ; le vrai risque (Service sans backend) passe par EXPOSES.
    "SELECTS": "none",
}

# Nœuds virtuels créés par le normalizer : ils ne « dépendent » de rien.
# (ex. Internet EXPOSES LoadBalancer : supprimer le LB ne casse pas Internet)
NEVER_DEPENDENT_TYPES = {"INTERNET"}


class DeletedDependencyRule(Rule):
    rule_id = "RULE-RES-003"
    version = "1.0"
    category = Category.RESILIENCE
    severity = Severity.MEDIUM
    title = "Suppression d'une ressource utilisée"
    remediation = (
        "Migrer ou supprimer d'abord les ressources dépendantes, "
        "ou conserver la ressource."
    )

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        deleted_ids = {c.resource_id for c in ctx.changes if c.operation == "delete"}

        for change in ctx.changes:
            if change.operation != "delete":
                continue

            rid = change.resource_id
            if ctx.current_twin.get(rid) is None:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"{rid} est introuvable dans le Current Twin.",
                    change_ids=[change.change_id],
                    affected=[rid],
                    evidence={"reason": "resource_not_in_current_twin"},
                ))
                continue

            dependents, unknown_types = self._dependents(ctx, rid)
            remaining = sorted(dependents - deleted_ids)
            evidence = {
                "deleted_resource": rid,
                "dependents": remaining,
                "dependents_also_deleted": sorted(dependents & deleted_ids),
            }
            if unknown_types:
                # On ne devine pas le sens d'une relation inconnue : on le signale.
                evidence["unknown_relationship_types"] = sorted(unknown_types)

            if remaining:
                findings.append(self.finding(
                    Status.FAIL,
                    f"{rid} est supprimé alors que {len(remaining)} ressource(s) en dépendent.",
                    change_ids=[change.change_id],
                    affected=[rid] + remaining,
                    evidence=evidence,
                ))
            elif unknown_types:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"{rid} a des relations de type inconnu : dépendances non vérifiables.",
                    change_ids=[change.change_id],
                    affected=[rid],
                    evidence=evidence,
                ))
            else:
                findings.append(self.finding(
                    Status.PASS,
                    f"Aucune ressource ne dépend de {rid}.",
                    change_ids=[change.change_id],
                    affected=[rid],
                    evidence=evidence,
                ))

        return findings

    @staticmethod
    def _dependents(ctx: AnalysisContext, rid: str) -> tuple[set[str], set[str]]:
        dependents: set[str] = set()
        unknown: set[str] = set()
        for rel in ctx.current_twin.relationships:
            if rid not in (rel.source, rel.target):
                continue
            direction = DEPENDENCY_DIRECTION.get(rel.type)
            if direction is None:
                unknown.add(rel.type)
            elif direction == "none":
                continue
            elif direction == "source" and rel.target == rid:
                dependents.add(rel.source)
            elif direction == "target" and rel.source == rid:
                dependents.add(rel.target)
        dependents = {
            d for d in dependents
            if (r := ctx.current_twin.get(d)) is None or r.type not in NEVER_DEPENDENT_TYPES
        }
        return dependents, unknown
