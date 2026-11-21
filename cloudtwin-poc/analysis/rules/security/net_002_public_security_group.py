"""RULE-NET-002 — Security Group élargi à Internet (0.0.0.0/0 ou ::/0).

Un Security Group qui protège un service interne ne doit pas accepter de
trafic depuis n'importe quelle adresse d'Internet.

Logique, pour chaque changement de l'ingress d'un SECURITY_GROUP :
  - FAIL (high)      si le changement ajoute au moins une entrée publique ;
  - FAIL (medium)    si rien n'est ajouté mais qu'une entrée publique existait
                     déjà (on ne masque jamais une exposition) ;
  - PASS             si aucune entrée n'est publique, ou si les seules nouvelles
                     entrées publiques sont HTTP/HTTPS (80/443) sur le SG d'un
                     load balancer internet-facing : c'est son rôle ;
  - NOT_EVALUATED    si l'état futur est inconnu (`after_unknown`) ou illisible.

Le champ `evidence.overlaps_with` indique si la même ouverture déclenche
aussi RULE-NET-001 (SSH) ou RULE-NET-003 (base de données). C'est au score
de décider s'il compte les deux.
"""

from __future__ import annotations

from analysis.models import AnalysisContext, Category, Finding, Severity, Status
from analysis.rules.base import Rule
from analysis.rules.security._network import (
    ingress_from_change,
    neighbours,
    overlapping_rules,
    parse_ingress,
)

SG_TYPE = "SECURITY_GROUP"
WEB_PORTS = (80, 443)


class PublicSecurityGroupRule(Rule):
    rule_id = "RULE-NET-002"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.HIGH
    title = "Security Group ouvert à Internet"
    remediation = (
        "Restreindre la source au CIDR du VPC ou, mieux, au Security Group "
        "de l'appelant (ALB, nœuds EKS) au lieu de 0.0.0.0/0."
    )
    reference = "Exposition potentielle associée à MITRE ATT&CK T1190"

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        for change in ctx.changes:
            if change.resource_type.upper() != SG_TYPE or change.operation == "delete":
                continue
            concerned, after_raw = ingress_from_change(change, "after")
            if not concerned:
                continue
            _, before_raw = ingress_from_change(change, "before")

            rid = change.resource_id
            affected = [rid] + self._users(ctx, change.change_id, rid)
            after = parse_ingress(after_raw)
            if after is None:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"Règles d'ingress futures inconnues ou illisibles pour {rid}.",
                    change_ids=[change.change_id], affected=[rid],
                    evidence={"property_path": change.property_path, "after": after_raw},
                ))
                continue

            # Un `before` illisible est traité comme vide : tout est alors « nouveau ».
            before = parse_ingress(before_raw) or []
            public_after = sorted({e for e in after if e.is_public})
            public_before = {e for e in before if e.is_public}
            newly = [e for e in public_after if e not in public_before]
            already = [e for e in public_after if e in public_before]

            evidence = {
                "property_path": change.property_path,
                "newly_exposed": [e.to_dict() for e in newly],
                "already_exposed_before": [e.to_dict() for e in already],
                "overlaps_with": overlapping_rules(public_after),
            }

            if newly and self._intended_web_entry(ctx, rid, newly):
                evidence["accepted_reason"] = "HTTP/HTTPS public sur le SG d'un load balancer internet-facing"
                findings.append(self.finding(
                    Status.PASS,
                    f"{rid} ouvre seulement 80/443 pour un load balancer public.",
                    change_ids=[change.change_id], affected=[rid], evidence=evidence,
                ))
            elif newly:
                findings.append(self.finding(
                    Status.FAIL,
                    f"{rid} devient accessible depuis Internet : {self._describe(newly)}.",
                    change_ids=[change.change_id], affected=affected, evidence=evidence,
                ))
            elif already:
                findings.append(self.finding(
                    Status.FAIL,
                    f"{rid} était déjà accessible depuis Internet : {self._describe(already)}.",
                    change_ids=[change.change_id], affected=affected, evidence=evidence,
                    severity=Severity.MEDIUM,
                ))
            else:
                findings.append(self.finding(
                    Status.PASS,
                    f"{rid} n'accepte aucune source publique.",
                    change_ids=[change.change_id], affected=[rid], evidence=evidence,
                ))

        return findings

    # ------------------------------------------------------------------ outils

    @staticmethod
    def _users(ctx: AnalysisContext, change_id: str, rid: str) -> list[str]:
        """Ressources qui utilisent ce SG (jumeau) + celles calculées par Hamdi."""
        users = neighbours(ctx.current_twin, rid, {"USES"})
        users += [a.target for a in ctx.affected_resources
                  if a.change_id == change_id and a.distance == 1]
        return users

    @staticmethod
    def _intended_web_entry(ctx: AnalysisContext, rid: str, newly) -> bool:
        only_web = all(e.protocol == "tcp" and e.from_port == e.to_port
                       and e.from_port in WEB_PORTS for e in newly)
        if not only_web:
            return False
        for other in neighbours(ctx.current_twin, rid, {"USES"}):
            res = ctx.current_twin.get(other)
            if res and res.type == "LOAD_BALANCER" and res.properties.get("scheme") == "internet-facing":
                return True
        return False

    @staticmethod
    def _describe(entries) -> str:
        parts = []
        for e in entries:
            ports = "tous les ports" if e.protocol == "all" else (
                f"{e.protocol}/{e.from_port}" if e.from_port == e.to_port
                else f"{e.protocol}/{e.from_port}-{e.to_port}")
            parts.append(f"{ports} depuis {e.cidr}")
        return ", ".join(parts)
