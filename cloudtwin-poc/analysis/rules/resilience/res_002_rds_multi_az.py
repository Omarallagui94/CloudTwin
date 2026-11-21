"""RULE-RES-002 — Perte de redondance d'une base RDS (Multi-AZ désactivé).

Avec Multi-AZ, RDS garde une copie synchrone dans une autre zone de
disponibilité et bascule dessus automatiquement en cas de panne ou de
maintenance. Sans Multi-AZ, la base devient un point unique de défaillance
pour toutes les applications qui l'utilisent.

Logique, pour chaque changement de `multi_az` d'un RDS_INSTANCE :
  - FAIL (high)    si multi_az passe de true à false ;
  - FAIL (medium)  si une nouvelle base est créée sans Multi-AZ ;
  - PASS           si multi_az reste ou devient true ;
  - NOT_EVALUATED  si la valeur future est inconnue (`after_unknown`) ou n'est
                   pas un booléen.

Les instances membres d'un cluster (Aurora, Multi-AZ DB cluster) sont ignorées :
leur disponibilité se règle au niveau du cluster, pas de l'instance.

Type de ressource : `RDS_INSTANCE` (proposition, pas encore dans le schéma v1).
"""

from __future__ import annotations

from typing import Any

from analysis.models import AnalysisContext, Category, Change, Finding, Severity, Status
from analysis.rules.base import Rule
from analysis.rules.security._network import neighbours

RDS_TYPES = {"RDS_INSTANCE"}
# Terraform (aws_db_instance.multi_az) et API AWS (DescribeDBInstances.MultiAZ).
MULTI_AZ_PATHS = {"properties.multi_az", "properties.MultiAZ"}
MULTI_AZ_KEYS = ("multi_az", "MultiAZ")
CLUSTER_KEYS = ("cluster_identifier", "db_cluster_identifier", "DBClusterIdentifier")


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


class RdsMultiAzRule(Rule):
    rule_id = "RULE-RES-002"
    version = "1.0"
    category = Category.RESILIENCE
    severity = Severity.HIGH
    title = "Perte de redondance RDS"
    remediation = "Réactiver Multi-AZ (multi_az = true) sur la base de production."
    reference = "AWS Security Hub RDS.5 · CIS AWS Foundations v4.0.1 2.2.4"

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        for change in ctx.changes:
            if change.resource_type.upper() not in RDS_TYPES or change.operation == "delete":
                continue
            values = self._values(change)
            if values is None:
                continue
            before_raw, after_raw = values
            rid = change.resource_id
            if self._in_cluster(ctx, change):
                continue

            before, after = _bool(before_raw), _bool(after_raw)
            evidence = {"property_path": change.property_path,
                        "before": before_raw, "after": after_raw}
            ids = [change.change_id]

            if after is None:
                findings.append(self.finding(
                    Status.NOT_EVALUATED, f"Valeur future de multi_az inconnue pour {rid}.",
                    change_ids=ids, affected=[rid], evidence=evidence))
            elif after:
                findings.append(self.finding(
                    Status.PASS, f"{rid} reste en Multi-AZ.",
                    change_ids=ids, affected=[rid], evidence=evidence))
            elif change.operation == "create":
                findings.append(self.finding(
                    Status.FAIL, f"{rid} est créée sans Multi-AZ.",
                    change_ids=ids, affected=self._affected(ctx, change), evidence=evidence,
                    severity=Severity.MEDIUM))
            elif before is False:
                # Déjà mono-AZ avant : pas de perte due à ce changement.
                findings.append(self.finding(
                    Status.PASS, f"{rid} était déjà sans Multi-AZ ; ce changement n'en retire pas.",
                    change_ids=ids, affected=[rid], evidence=evidence))
            else:
                findings.append(self.finding(
                    Status.FAIL, f"{rid} perd sa redondance : multi_az passe de {before_raw} à false.",
                    change_ids=ids, affected=self._affected(ctx, change), evidence=evidence))

        return findings

    # ------------------------------------------------------------------ outils

    @staticmethod
    def _values(change: Change) -> tuple[Any, Any] | None:
        """(before, after) de multi_az, ou None si le changement ne le touche pas."""
        if change.property_path in MULTI_AZ_PATHS:
            return change.before, change.after
        if change.property_path in (None, "properties") and change.operation in ("create", "replace"):
            def pick(d):
                if not isinstance(d, dict):
                    return None
                return next((d[k] for k in MULTI_AZ_KEYS if k in d), None)
            return pick(change.before), pick(change.after)
        return None

    @staticmethod
    def _in_cluster(ctx: AnalysisContext, change: Change) -> bool:
        props: dict[str, Any] = {}
        current = ctx.current_twin.get(change.resource_id)
        if current:
            props.update(current.properties)
        if isinstance(change.after, dict):
            props.update(change.after)
        return any(props.get(k) for k in CLUSTER_KEYS)

    @staticmethod
    def _affected(ctx: AnalysisContext, change: Change) -> list[str]:
        """La base et ce qui l'utilise : jumeau + ressources calculées par Hamdi."""
        rid = change.resource_id
        out = [rid] + neighbours(ctx.current_twin, rid, {"USES", "CONNECTS_TO", "DEPENDS_ON"})
        out += [a.target for a in ctx.affected_resources
                if a.change_id == change.change_id and a.distance == 1]
        return out
