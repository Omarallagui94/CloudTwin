"""RULE-NET-001 — Port d'administration à distance ouvert à Internet.

SSH (22) et RDP (3389) ouverts à 0.0.0.0/0 ou ::/0 exposent l'accès
administrateur des machines à des tentatives de connexion depuis n'importe où.

Logique, pour chaque changement de l'ingress d'un SECURITY_GROUP :
  - FAIL (high)      si le changement ouvre 22 ou 3389 à une source publique
                     (TCP ou « tous protocoles ») ;
  - FAIL (medium)    si rien n'est ajouté mais que l'ouverture existait déjà ;
  - PASS             si aucune source publique n'atteint ces ports ;
  - NOT_EVALUATED    si l'état futur est inconnu (`after_unknown`) ou illisible.

Références vérifiées : CIS AWS Foundations v4.0.1 5.3 (IPv4) et 5.4 (IPv6) ;
exposition potentielle associée à MITRE ATT&CK T1190.
"""

from __future__ import annotations

from analysis.models import AnalysisContext, Category, Finding, Severity, Status
from analysis.rules.base import Rule
from analysis.rules.security._network import ingress_from_change, neighbours, parse_ingress

SG_TYPE = "SECURITY_GROUP"
ADMIN_PORTS = {22: "SSH", 3389: "RDP"}


class PublicAdminPortRule(Rule):
    rule_id = "RULE-NET-001"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.HIGH
    title = "Port d'administration ouvert à Internet"
    remediation = (
        "Fermer 22/3389 à Internet : passer par AWS Systems Manager Session Manager "
        "ou un bastion, ou limiter la source à une adresse d'administration précise."
    )
    reference = ("CIS AWS Foundations v4.0.1 5.3 / 5.4 · "
                 "Exposition potentielle associée à MITRE ATT&CK T1190")

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
            ids = [change.change_id]
            after = parse_ingress(after_raw)
            if after is None:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"Règles d'ingress futures inconnues ou illisibles pour {rid}.",
                    change_ids=ids, affected=[rid],
                    evidence={"property_path": change.property_path, "after": after_raw}))
                continue

            before = parse_ingress(before_raw) or []
            exposed_after = self._exposed(after)
            exposed_before = self._exposed(before)
            newly = sorted(exposed_after - exposed_before)
            already = sorted(exposed_after & exposed_before)

            evidence = {
                "property_path": change.property_path,
                "newly_exposed": [{"port": p, "service": ADMIN_PORTS[p], "cidr": c} for p, c in newly],
                "already_exposed_before": [{"port": p, "service": ADMIN_PORTS[p], "cidr": c}
                                           for p, c in already],
            }
            affected = [rid] + neighbours(ctx.current_twin, rid, {"USES"}) + [
                a.target for a in ctx.affected_resources
                if a.change_id == change.change_id and a.distance == 1]

            if newly:
                findings.append(self.finding(
                    Status.FAIL, f"{rid} ouvre {self._describe(newly)} à Internet.",
                    change_ids=ids, affected=affected, evidence=evidence))
            elif already:
                findings.append(self.finding(
                    Status.FAIL, f"{rid} laisse {self._describe(already)} ouvert à Internet.",
                    change_ids=ids, affected=affected, evidence=evidence,
                    severity=Severity.MEDIUM))
            else:
                findings.append(self.finding(
                    Status.PASS, f"{rid} n'ouvre ni SSH ni RDP à Internet.",
                    change_ids=ids, affected=[rid], evidence=evidence))

        return findings

    @staticmethod
    def _exposed(entries) -> set[tuple[int, str]]:
        return {(port, e.cidr) for e in entries if e.is_public
                for port in ADMIN_PORTS if e.covers(port)}

    @staticmethod
    def _describe(pairs) -> str:
        ports = sorted({p for p, _ in pairs})
        return " et ".join(f"{ADMIN_PORTS[p]} ({p})" for p in ports)
