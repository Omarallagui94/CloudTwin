"""Boucle commune aux règles IAM : comparer ce qu'un rôle autorise avant et après.

Une sous-classe définit seulement `grants(statements)` : l'ensemble des
« droits dangereux » qu'elle cherche. La comparaison, les statuts et les
ressources impactées sont identiques pour toutes les règles IAM.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from analysis.models import AnalysisContext, Finding, Severity, Status
from analysis.rules.base import Rule
from analysis.rules.security._iam import Statement, parse_policies, policies_from_change
from analysis.rules.security._network import neighbours

IAM_TYPES = {"IAM_ROLE"}


class IamGrantRule(Rule):

    @abstractmethod
    def grants(self, statements: list[Statement]) -> dict[tuple, dict[str, Any]]:
        """{clé du droit: détail pour l'evidence}."""

    def severity_for(self, new_details: list[dict[str, Any]]) -> Severity:
        return self.severity

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        for change in ctx.changes:
            if change.resource_type.upper() not in IAM_TYPES or change.operation == "delete":
                continue
            concerned, after_raw = policies_from_change(change, "after")
            if not concerned:
                continue
            _, before_raw = policies_from_change(change, "before")

            rid = change.resource_id
            ids = [change.change_id]
            after = parse_policies(after_raw)
            if after is None:
                findings.append(self.finding(
                    Status.NOT_EVALUATED, f"Politiques futures inconnues ou illisibles pour {rid}.",
                    change_ids=ids, affected=[rid],
                    evidence={"property_path": change.property_path}))
                continue

            after_statements, unknown = after
            before_statements = (parse_policies(before_raw) or ([], []))[0]
            granted_after = self.grants(after_statements)
            granted_before = self.grants(before_statements)
            newly = [granted_after[k] for k in sorted(granted_after) if k not in granted_before]
            already = [granted_after[k] for k in sorted(granted_after) if k in granted_before]

            evidence = {
                "property_path": change.property_path,
                "newly_granted": newly,
                "already_granted_before": already,
                "unknown_policies": sorted(unknown),
            }
            affected = [rid] + neighbours(ctx.current_twin, rid) + [
                a.target for a in ctx.affected_resources
                if a.change_id == change.change_id and a.distance == 1]

            if newly:
                findings.append(self.finding(
                    Status.FAIL, self.describe(rid, newly, new=True),
                    change_ids=ids, affected=affected, evidence=evidence,
                    severity=self.severity_for(newly)))
            elif already:
                findings.append(self.finding(
                    Status.FAIL, self.describe(rid, already, new=False),
                    change_ids=ids, affected=affected, evidence=evidence,
                    severity=Severity.MEDIUM))
            elif unknown:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"Contenu inconnu pour {', '.join(sorted(unknown))} sur {rid}.",
                    change_ids=ids, affected=[rid], evidence=evidence))
            else:
                findings.append(self.finding(
                    Status.PASS, f"{rid} : {self.pass_text}.",
                    change_ids=ids, affected=[rid], evidence=evidence))

        return findings

    pass_text = "aucun droit dangereux"

    @abstractmethod
    def describe(self, rid: str, details: list[dict[str, Any]], *, new: bool) -> str:
        ...
