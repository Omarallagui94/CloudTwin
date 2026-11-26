"""RULE-RES-001 — Workload réduit à moins de 2 réplicas.

Un seul réplica = point unique de défaillance : si le Pod tombe (nœud perdu,
mise à jour, OOM), le service est indisponible.

Logique :
  - on regarde les changements sur `properties.replicas` des workloads ;
  - FAIL           si after < 2 et que le nombre de réplicas diminue
                   (ou si la valeur précédente est inconnue) ;
  - PASS           si after >= 2, ou si la valeur ne diminue pas ;
  - NOT_EVALUATED  si la nouvelle valeur est absente ou n'est pas un entier.
"""

from __future__ import annotations

from analysis.models import AnalysisContext, Category, Finding, Severity, Status
from analysis.rules.base import Rule

# Types exacts produits par cloudtwin-poc/discovery/normalizer.py
WORKLOAD_TYPES = {"DEPLOYMENT", "STATEFUL_SET"}
REPLICAS_PATH = "properties.replicas"
MIN_REPLICAS = 2


def _as_int(value) -> int | None:
    # bool est un sous-type de int en Python : on l'exclut volontairement.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class SingleReplicaRule(Rule):
    rule_id = "RULE-RES-001"
    version = "1.0"
    category = Category.RESILIENCE
    severity = Severity.HIGH
    title = "Point unique de défaillance"
    remediation = f"Garder au moins {MIN_REPLICAS} réplicas en production."

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        for change in ctx.changes:
            if change.resource_type.upper() not in WORKLOAD_TYPES:
                continue
            if change.operation not in ("update", "create", "replace"):
                continue
            if change.property_path != REPLICAS_PATH:
                continue

            before = _as_int(change.before)
            after = _as_int(change.after)
            evidence = {
                "property_path": REPLICAS_PATH,
                "before": change.before,
                "after": change.after,
                "threshold": MIN_REPLICAS,
            }

            if after is None:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"Nombre de réplicas futur inconnu pour {change.resource_id}.",
                    change_ids=[change.change_id],
                    affected=[change.resource_id],
                    evidence=evidence,
                ))
                continue

            decreases = before is None or after < before
            if after < MIN_REPLICAS and decreases:
                # 0 réplica = arrêt du service, pas seulement un risque.
                severity = Severity.CRITICAL if after == 0 else self.severity
                findings.append(self.finding(
                    Status.FAIL,
                    f"{change.resource_id} passe de {change.before} à {after} réplica(s).",
                    change_ids=[change.change_id],
                    affected=[change.resource_id] + self._exposing(ctx, change.change_id),
                    evidence=evidence,
                    severity=severity,
                ))
            else:
                findings.append(self.finding(
                    Status.PASS,
                    f"{change.resource_id} conserve {after} réplica(s).",
                    change_ids=[change.change_id],
                    affected=[change.resource_id],
                    evidence=evidence,
                ))

        return findings

    @staticmethod
    def _exposing(ctx: AnalysisContext, change_id: str) -> list[str]:
        """Ressources directement impactées, d'après le résultat de Hamdi."""
        return [
            a.target for a in ctx.affected_resources
            if a.change_id == change_id and a.distance == 1
        ]
