"""RULE-IAM-002 — Ajout d'une permission IAM sensible.

Certaines actions IAM permettent à un rôle de se donner, ou de donner à un
autre rôle, plus de droits qu'il n'en a : c'est une escalade de privilèges.
La plus connue est `iam:PassRole` : avec elle, le rôle peut confier un rôle
plus puissant à un service (EC2, Lambda, EKS) qu'il contrôle.

Logique, pour chaque changement des politiques d'un IAM_ROLE :
  - FAIL (high)    si le changement ajoute une action de SENSITIVE_ACTIONS
                   sur une ressource large (contenant « * ») ;
  - FAIL (medium)  si l'ajout est limité à des ressources précises, ou si la
                   permission existait déjà ;
  - PASS           si aucune action sensible n'est accordée ;
  - NOT_EVALUATED  si les politiques futures sont illisibles ou inconnues.

Les wildcards sont pris en compte (`iam:*`, `iam:Pass*`, `NotAction`).
Une déclaration *:* complète est laissée à RULE-IAM-001, pour ne pas compter
deux fois le même risque.

Référence vérifiée : escalade de privilèges potentielle associée à
MITRE ATT&CK T1098.003 (Additional Cloud Roles).
"""

from __future__ import annotations

from typing import Any

from analysis.models import Category, Severity
from analysis.rules.security._iam import Statement
from analysis.rules.security._iam_rule import IamGrantRule

# Actions connues pour permettre une escalade de privilèges dans AWS IAM.
SENSITIVE_ACTIONS = (
    "iam:PassRole",
    "iam:CreatePolicyVersion",
    "iam:SetDefaultPolicyVersion",
    "iam:AttachRolePolicy",
    "iam:AttachUserPolicy",
    "iam:AttachGroupPolicy",
    "iam:PutRolePolicy",
    "iam:PutUserPolicy",
    "iam:PutGroupPolicy",
    "iam:UpdateAssumeRolePolicy",
    "iam:CreateAccessKey",
    "iam:CreateLoginProfile",
    "iam:UpdateLoginProfile",
    "iam:AddUserToGroup",
)


class SensitivePermissionRule(IamGrantRule):
    rule_id = "RULE-IAM-002"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.HIGH
    title = "Permission IAM sensible ajoutée"
    remediation = ("Retirer la permission, ou la limiter à un rôle précis (ARN exact au lieu "
                   "de « * ») avec une condition iam:PassedToService.")
    reference = ("Escalade de privilèges potentielle associée à MITRE ATT&CK T1098.003 "
                 "(Additional Cloud Roles)")
    pass_text = "aucune permission IAM sensible"

    def grants(self, statements: list[Statement]) -> dict[tuple, dict[str, Any]]:
        out: dict[tuple, dict[str, Any]] = {}
        for st in statements:
            if st.is_full_admin:
                continue        # compté par RULE-IAM-001
            for action in SENSITIVE_ACTIONS:
                if st.allows(action):
                    out[(st.policy, action, st.resources)] = {
                        "policy": st.policy, "action": action,
                        "resources": list(st.resources), "broad_resource": st.broad_resource,
                        "has_condition": st.has_condition,
                    }
        return out

    def severity_for(self, new_details: list[dict[str, Any]]) -> Severity:
        return Severity.HIGH if any(d["broad_resource"] for d in new_details) else Severity.MEDIUM

    def describe(self, rid: str, details: list[dict[str, Any]], *, new: bool) -> str:
        actions = ", ".join(sorted({d["action"] for d in details}))
        verb = "reçoit" if new else "garde"
        return f"{rid} {verb} des permissions sensibles : {actions}."
