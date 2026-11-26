"""Outils communs aux règles IAM (RULE-IAM-*).

Les politiques d'un rôle arrivent sous plusieurs formes :
  - découverte d'Omar (nœud IAM_ROLE) :
      properties.attached_policies = [{name, arn, document}]
      properties.inline_policies   = [{name, document}]
  - Terraform `aws_iam_role` (ingestion de Hamdi) :
      properties.inline_policy       = [{name, policy: "<JSON en texte>"}]
      properties.managed_policy_arns = ["arn:aws:iam::aws:policy/..."]

Ce module les ramène à une liste de déclarations (Statement) lisibles.
Une politique dont on n'a pas le contenu (ARN seul, inconnu) est
signalée comme « inconnue » : la règle ne conclut alors jamais `pass`.

Limite connue : les `Deny` explicites et les `Condition` ne sont pas
déduits des `Allow`. L'analyse est volontairement prudente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from analysis.models import Change

POLICY_PATHS = {
    "properties.attached_policies",
    "properties.inline_policies",
    "properties.inline_policy",
    "properties.managed_policy_arns",
}
POLICY_KEYS = ("attached_policies", "inline_policies", "inline_policy", "managed_policy_arns")

# Politiques gérées par AWS dont le contenu est connu sans le télécharger.
KNOWN_MANAGED = {
    "AdministratorAccess": {"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]},
    "IAMFullAccess": {"Statement": [{"Effect": "Allow", "Action": "iam:*", "Resource": "*"}]},
}


@dataclass(frozen=True)
class Statement:
    policy: str                    # nom ou ARN de la politique d'origine
    actions: tuple[str, ...]       # en minuscules ; vide si NotAction
    not_actions: tuple[str, ...]
    resources: tuple[str, ...]
    has_condition: bool

    @property
    def is_full_admin(self) -> bool:
        return "*" in self.actions and "*" in self.resources

    def allows(self, action: str) -> bool:
        action = action.lower()
        if self.not_actions:
            return not any(fnmatchcase(action, p) for p in self.not_actions)
        return any(fnmatchcase(action, p) for p in self.actions)

    @property
    def broad_resource(self) -> bool:
        return any("*" in r for r in self.resources)


def _as_list(value: Any) -> list | None:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def _document(item: Any) -> tuple[str, dict | None]:
    """(étiquette, document) pour un élément de politique, document None si inconnu."""
    if isinstance(item, str):                      # managed_policy_arns
        name = item.rsplit("/", 1)[-1]
        known = KNOWN_MANAGED.get(name) if ":iam::aws:policy/" in item else None
        return item, known
    if not isinstance(item, dict):
        return "?", None
    label = item.get("arn") or item.get("name") or "?"
    if item.get("document_available") is False:
        return label, None
    doc = item.get("document", item.get("policy"))
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError:
            return label, None
    if not isinstance(doc, dict):
        arn = item.get("arn") or ""
        name = arn.rsplit("/", 1)[-1]
        return label, KNOWN_MANAGED.get(name) if ":iam::aws:policy/" in arn else None
    return label, doc


def _statements(label: str, doc: dict) -> list[Statement] | None:
    raw = doc.get("Statement")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    out = []
    for st in raw:
        if not isinstance(st, dict):
            return None
        if st.get("Effect") != "Allow":
            continue
        actions = _as_list(st.get("Action"))
        not_actions = _as_list(st.get("NotAction"))
        resources = _as_list(st.get("Resource"))
        if actions is None or not_actions is None or resources is None:
            return None
        if not resources and st.get("NotResource") is not None:
            resources = ["*"]          # NotResource = presque tout : traité comme large
        out.append(Statement(
            policy=label,
            actions=tuple(a.lower() for a in actions),
            not_actions=tuple(a.lower() for a in not_actions),
            resources=tuple(resources),
            has_condition=bool(st.get("Condition")),
        ))
    return out


def policies_from_change(change: Change, side: str) -> tuple[bool, Any]:
    """(concerné, liste brute de politiques) pour le côté `before` ou `after`."""
    value = getattr(change, side)
    if change.property_path in POLICY_PATHS:
        return True, value
    if change.property_path in (None, "properties") and change.operation in ("create", "replace"):
        if value is None:
            return True, [] if side == "before" else None
        if isinstance(value, dict):
            found = [k for k in POLICY_KEYS if k in value]
            if not found:
                return False, None
            items: list = []
            for k in found:
                if not isinstance(value[k], list):
                    return True, None
                items += value[k]
            return True, items
    return False, None


def parse_policies(items: Any) -> tuple[list[Statement], list[str]] | None:
    """(déclarations Allow, politiques au contenu inconnu), ou None si illisible."""
    if items is None or not isinstance(items, list):
        return None
    statements: list[Statement] = []
    unknown: list[str] = []
    for item in items:
        label, doc = _document(item)
        if doc is None:
            unknown.append(label)
            continue
        parsed = _statements(label, doc)
        if parsed is None:
            unknown.append(label)
            continue
        statements += parsed
    return statements, unknown
