"""Classe de base commune à toutes les règles.

Une règle est une fonction pure : même contexte en entrée, mêmes findings en
sortie. Elle ne parle ni à Neo4j, ni à AWS, ni au LLM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from analysis.models import AnalysisContext, Category, Finding, Severity, Status


class Rule(ABC):
    rule_id: str
    version: str
    category: Category
    severity: Severity
    title: str
    remediation: str
    reference: str | None = None

    @abstractmethod
    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        """Retourne un finding par élément évalué (fail, pass ou not_evaluated).

        Retourner une liste vide signifie : « la règle ne s'applique à aucun
        changement de cette simulation ».
        """

    # Petit utilitaire pour que toutes les règles produisent le même format.
    def finding(
        self,
        status: Status,
        description: str,
        *,
        change_ids: list[str] | None = None,
        affected: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        severity: Severity | None = None,
    ) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            rule_version=self.version,
            status=status,
            category=self.category,
            severity=severity or self.severity,
            title=self.title,
            description=description,
            change_ids=sorted(change_ids or []),
            affected_resource_ids=sorted(set(affected or [])),
            evidence=evidence or {},
            remediation=self.remediation if status == Status.FAIL else None,
            reference=self.reference,
        )
