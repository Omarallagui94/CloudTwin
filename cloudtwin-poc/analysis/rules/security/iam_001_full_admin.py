"""RULE-IAM-001 — Politique IAM trop permissive (« *:* »).

Une déclaration `"Effect": "Allow"`, `"Action": "*"` sur `"Resource": "*"`
donne au rôle tous les droits sur tout le compte AWS. Si le rôle est
compromis (Pod, instance, pipeline), le compte entier l'est aussi.

Logique, pour chaque changement des politiques d'un IAM_ROLE :
  - FAIL (critical)  si le changement ajoute une telle déclaration (en ligne,
                     ou via la politique gérée AdministratorAccess) ;
  - FAIL (medium)    si elle existait déjà et reste en place ;
  - PASS             si aucune politique n'en contient ;
  - NOT_EVALUATED    si les politiques futures sont illisibles, ou si une
                     politique au contenu inconnu empêche de conclure.

Références vérifiées : CIS AWS Foundations v4.0.1 1.16 ; AWS Security Hub IAM.1.
"""

from __future__ import annotations

from typing import Any

from analysis.models import Category, Severity
from analysis.rules.security._iam import Statement
from analysis.rules.security._iam_rule import IamGrantRule


class FullAdminPolicyRule(IamGrantRule):
    rule_id = "RULE-IAM-001"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.CRITICAL
    title = "Politique IAM trop permissive (*:*)"
    remediation = ("Remplacer \"Action\": \"*\" / \"Resource\": \"*\" par les seules actions "
                   "et ressources nécessaires (moindre privilège).")
    reference = "CIS AWS Foundations v4.0.1 1.16 · AWS Security Hub IAM.1"
    pass_text = "aucune politique n'accorde *:*"

    def grants(self, statements: list[Statement]) -> dict[tuple, dict[str, Any]]:
        return {
            (st.policy,): {"policy": st.policy, "action": "*", "resource": "*",
                           "has_condition": st.has_condition}
            for st in statements if st.is_full_admin
        }

    def describe(self, rid: str, details: list[dict[str, Any]], *, new: bool) -> str:
        names = ", ".join(d["policy"] for d in details)
        verb = "reçoit" if new else "garde"
        return f"{rid} {verb} les droits administrateur complets (*:*) via {names}."
