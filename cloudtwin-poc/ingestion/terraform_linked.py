"""Ressources Terraform « rattachées » : elles modifient une autre ressource du jumeau.

Terraform déclare souvent à part ce qui, dans le jumeau, est une propriété d'une
autre ressource :

  aws_iam_role_policy                   → politique en ligne d'un IAM_ROLE
  aws_iam_role_policy_attachment        → politique attachée à un IAM_ROLE
  aws_iam_policy                        → document d'une politique déjà attachée à des rôles
  aws_security_group_rule (ingress)     → règle d'ingress d'un SECURITY_GROUP
  aws_vpc_security_group_ingress_rule   → règle d'ingress d'un SECURITY_GROUP

Chaque ressource devient donc un changement `update` sur le rôle ou le Security
Group visé, aux chemins que les règles lisent déjà (`properties.inline_policy`,
`properties.attached_policies`, `properties.ingress`). Les listes sont cumulées :
deux politiques ajoutées au même rôle dans un plan donnent deux changements
successifs, le second partant du résultat du premier.

Quand une valeur n'est connue qu'après `apply` (ARN d'une politique créée dans le
même plan, ID d'un Security Group créé dans le même plan), la cible est retrouvée
grâce aux références de `configuration.root_module`. Si elle reste introuvable, la
ressource est signalée dans `terraform_coverage.unsupported_material` : jamais
devinée.
"""

from __future__ import annotations

import copy
from typing import Any

from ingestion.models import NormalizedChange, OperationType
from ingestion.resolver import CanonicalResourceResolver

IAM_ROLE_POLICY = "aws_iam_role_policy"
IAM_ATTACHMENT = "aws_iam_role_policy_attachment"
IAM_POLICY = "aws_iam_policy"
SG_RULE = "aws_security_group_rule"
SG_INGRESS_RULE = "aws_vpc_security_group_ingress_rule"
SG_EGRESS_RULE = "aws_vpc_security_group_egress_rule"

LINKED_TYPES = frozenset({IAM_ROLE_POLICY, IAM_ATTACHMENT, IAM_POLICY, SG_RULE, SG_INGRESS_RULE, SG_EGRESS_RULE})


class LinkedResourceError(ValueError):
    """La ressource visée est introuvable : signalée dans la couverture, jamais devinée."""


class Ignored(Exception):
    """Ressource sans effet sur les règles (ex. règle egress) : couverture « ignored »."""


class LinkedResources:
    """Transforme les ressources rattachées d'un plan en changements sur leur cible."""

    def __init__(self, plan: dict[str, Any], resolver: CanonicalResourceResolver) -> None:
        self.resolver = resolver
        self.changes_by_address = {rc.get("address"): rc for rc in plan.get("resource_changes", [])}
        self.expressions = {
            r.get("address"): r.get("expressions") or {}
            for r in ((plan.get("configuration") or {}).get("root_module") or {}).get("resources", [])
        }
        # État courant des listes modifiées (rôle ou SG, clé) : les changements s'enchaînent.
        self.state: dict[tuple[str, str], list[Any]] = {}

    # ------------------------------------------------------------------ outils

    def _reference(self, address: str, attribute: str, target_type: str) -> dict[str, Any] | None:
        """La ressource du plan référencée par `attribute` (ex. aws_iam_role.app)."""
        refs = (self.expressions.get(address, {}).get(attribute) or {}).get("references") or []
        for ref in refs:
            parts = ref.split(".")
            if len(parts) >= 2 and parts[0] == target_type:
                rc = self.changes_by_address.get(f"{parts[0]}.{parts[1]}")
                if rc:
                    return rc
        return None

    @staticmethod
    def _side(rc: dict[str, Any], side: str) -> dict[str, Any]:
        return (rc.get("change") or {}).get(side) or {}

    def _current(self, target_id: str, key: str) -> list[Any]:
        if (target_id, key) not in self.state:
            props = (self.resolver.resources_by_id.get(target_id) or {}).get("properties") or {}
            value = props.get(key)
            self.state[(target_id, key)] = copy.deepcopy(value) if isinstance(value, list) else []
        return self.state[(target_id, key)]

    def _change(self, idx: int, target_id: str, target_type: str, key: str,
                after: list[Any], rc: dict[str, Any]) -> NormalizedChange:
        before = copy.deepcopy(self._current(target_id, key))
        self.state[(target_id, key)] = copy.deepcopy(after)
        return NormalizedChange(
            change_id=f"change-tf-{idx:03d}",
            operation=OperationType.UPDATE,
            resource_id=target_id,
            resource_type=target_type,
            provider="aws",
            property_path=f"properties.{key}",
            before=before,
            after=after,
            source={"kind": "terraform_plan", "reference": rc.get("address"), "type": rc.get("type"),
                    "name": rc.get("name"), "actions": (rc.get("change") or {}).get("actions", []),
                    "linked_to": target_id},
            namespace=None,
        )

    # ------------------------------------------------------------- IAM : rôle

    def _role_id(self, rc: dict[str, Any], operation: OperationType) -> str:
        side = self._side(rc, "before" if operation == OperationType.DELETE else "after")
        role = side.get("role")
        if isinstance(role, str) and role:
            name = role.rsplit("/", 1)[-1] if role.startswith("arn:") else role
            found = self.resolver.aws_name_index.get(("IAM_ROLE", name)) \
                or self.resolver.native_id_index.get(role)
            return found or f"aws:iam-role:{name}"
        role_rc = self._reference(rc.get("address"), "role", "aws_iam_role")
        if role_rc:
            planned = self._side(role_rc, "after") or self._side(role_rc, "before")
            name = planned.get("name") or role_rc.get("name")
            return self.resolver.aws_name_index.get(("IAM_ROLE", name)) or f"aws:iam-role:{name}"
        raise LinkedResourceError(f"rôle IAM de {rc.get('address')} introuvable (ni nom ni référence)")

    def _policy_item(self, rc: dict[str, Any], operation: OperationType) -> dict[str, Any]:
        """Politique attachée par un aws_iam_role_policy_attachment : nom, ARN et document si connu."""
        side = self._side(rc, "before" if operation == OperationType.DELETE else "after")
        arn = side.get("policy_arn")
        if isinstance(arn, str) and arn:
            item: dict[str, Any] = {"name": arn.rsplit("/", 1)[-1], "arn": arn}
            document = self._known_document(arn)
            if document is not None:
                item["document"] = document
            return item
        policy_rc = self._reference(rc.get("address"), "policy_arn", IAM_POLICY)
        if policy_rc:
            planned = self._side(policy_rc, "after")
            item = {"name": planned.get("name") or policy_rc.get("name"),
                    "arn": f"(connu après apply : {policy_rc.get('address')})"}
            document = planned.get("policy")
            if isinstance(document, str):
                item["policy"] = document
            else:
                item["document_available"] = False
            return item
        raise LinkedResourceError(f"politique attachée par {rc.get('address')} introuvable")

    def _known_document(self, arn: str) -> Any:
        """Document d'une politique déjà connue du jumeau (attachée à un autre rôle)."""
        for resource in self.resolver.resources_by_id.values():
            for item in (resource.get("properties") or {}).get("attached_policies") or []:
                if isinstance(item, dict) and item.get("arn") == arn and item.get("document") is not None:
                    return copy.deepcopy(item["document"])
        return None

    def _role_policy(self, rc: dict[str, Any], operation: OperationType, idx: int) -> list[NormalizedChange]:
        role_id = self._role_id(rc, operation)
        before, after = self._side(rc, "before"), self._side(rc, "after")
        name = after.get("name") or before.get("name") or rc.get("name")
        current = [p for p in self._current(role_id, "inline_policy")
                   if not (isinstance(p, dict) and p.get("name") == name)]
        if operation != OperationType.DELETE:
            current.append({"name": name, "policy": after.get("policy")})
        return [self._change(idx, role_id, "IAM_ROLE", "inline_policy", current, rc)]

    def _attachment(self, rc: dict[str, Any], operation: OperationType, idx: int) -> list[NormalizedChange]:
        role_id = self._role_id(rc, operation)
        item = self._policy_item(rc, operation)
        current = [p for p in self._current(role_id, "attached_policies")
                   if not (isinstance(p, dict) and p.get("arn") == item["arn"])]
        if operation != OperationType.DELETE:
            current.append(item)
        return [self._change(idx, role_id, "IAM_ROLE", "attached_policies", current, rc)]

    def _managed_policy(self, rc: dict[str, Any], operation: OperationType, idx: int) -> list[NormalizedChange]:
        """aws_iam_policy modifiée : nouveau document pour chaque rôle du jumeau qui l'a attachée."""
        arn = self._side(rc, "before").get("arn") or self._side(rc, "after").get("arn")
        if operation == OperationType.CREATE or not arn:
            raise Ignored("politique seule : évaluée au moment où un attachement la relie à un rôle")
        out = []
        for role_id, resource in self.resolver.resources_by_id.items():
            if resource.get("type") != "IAM_ROLE":
                continue
            attached = self._current(role_id, "attached_policies")
            if not any(isinstance(p, dict) and p.get("arn") == arn for p in attached):
                continue
            updated = []
            for p in attached:
                if isinstance(p, dict) and p.get("arn") == arn:
                    if operation == OperationType.DELETE:
                        continue
                    p = {k: v for k, v in p.items() if k not in ("document", "document_available")}
                    p["policy"] = self._side(rc, "after").get("policy")
                updated.append(p)
            out.append(self._change(idx + len(out), role_id, "IAM_ROLE", "attached_policies", updated, rc))
        if not out:
            raise Ignored("politique attachée à aucun rôle du jumeau")
        return out

    # ------------------------------------------------------ Security Groups

    def _sg_id(self, rc: dict[str, Any], operation: OperationType) -> str:
        side = self._side(rc, "before" if operation == OperationType.DELETE else "after")
        sg = side.get("security_group_id")
        if isinstance(sg, str) and sg:
            found = self.resolver.native_id_index.get(sg)
            if found:
                return found
            raise LinkedResourceError(f"Security Group {sg} de {rc.get('address')} absent du jumeau")
        sg_rc = self._reference(rc.get("address"), "security_group_id", "aws_security_group")
        if sg_rc:
            planned = self._side(sg_rc, "after") or self._side(sg_rc, "before")
            name = (planned.get("tags") or {}).get("Name") or planned.get("name") or sg_rc.get("name")
            return self.resolver.aws_name_index.get(("SECURITY_GROUP", name)) or f"aws:security-group:{name}"
        raise LinkedResourceError(f"Security Group de {rc.get('address')} introuvable")

    @staticmethod
    def _ingress_entry(rc_type: str, side: dict[str, Any]) -> dict[str, Any]:
        if rc_type == SG_RULE:
            return {"protocol": side.get("protocol"), "from_port": side.get("from_port"),
                    "to_port": side.get("to_port"), "cidr_blocks": side.get("cidr_blocks") or [],
                    "ipv6_cidr_blocks": side.get("ipv6_cidr_blocks") or []}
        protocol = str(side.get("ip_protocol", "-1"))
        all_ports = protocol in ("-1", "all")
        return {"protocol": "-1" if all_ports else protocol,
                "from_port": 0 if all_ports else side.get("from_port"),
                "to_port": 0 if all_ports else side.get("to_port"),
                "cidr_blocks": [side["cidr_ipv4"]] if side.get("cidr_ipv4") else [],
                "ipv6_cidr_blocks": [side["cidr_ipv6"]] if side.get("cidr_ipv6") else []}

    def _sg_rule(self, rc: dict[str, Any], operation: OperationType, idx: int) -> list[NormalizedChange]:
        rc_type = rc.get("type")
        if rc_type == SG_EGRESS_RULE:
            raise Ignored("règle de sortie (egress) : aucune règle CloudTwin ne l'analyse")
        before, after = self._side(rc, "before"), self._side(rc, "after")
        if rc_type == SG_RULE and (after or before).get("type", "ingress") != "ingress":
            raise Ignored("règle de sortie (egress) : aucune règle CloudTwin ne l'analyse")
        sg_id = self._sg_id(rc, operation)
        current = list(self._current(sg_id, "ingress"))
        if before and operation != OperationType.CREATE:
            old = self._ingress_entry(rc_type, before)
            current = [e for e in current if e != old]
        if operation != OperationType.DELETE:
            current.append(self._ingress_entry(rc_type, after))
        return [self._change(idx, sg_id, "SECURITY_GROUP", "ingress", current, rc)]

    # ----------------------------------------------------------------- entrée

    def changes_for(self, rc: dict[str, Any], operation: OperationType, idx: int) -> list[NormalizedChange]:
        rc_type = rc.get("type")
        if rc_type == IAM_ROLE_POLICY:
            return self._role_policy(rc, operation, idx)
        if rc_type == IAM_ATTACHMENT:
            return self._attachment(rc, operation, idx)
        if rc_type == IAM_POLICY:
            return self._managed_policy(rc, operation, idx)
        return self._sg_rule(rc, operation, idx)

