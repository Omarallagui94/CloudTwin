"""Catalogue de correctifs déterministes : une nouvelle valeur `after` par règle en échec.

Un correctif ne touche qu'un seul changement : il remplace sa valeur `after`
par une valeur sûre, ou annule le changement (REVERT). Il ne décide rien :
c'est le rejeu dans le jumeau (verify.py) qui dit s'il fait baisser le risque.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable

from analysis.models import AnalysisContext, Change, Finding
from analysis.rules.security._iam import KNOWN_MANAGED, parse_policies
from analysis.rules.security._network import PUBLIC_CIDRS
from analysis.rules.security.iam_001_full_admin import FullAdminPolicyRule
from analysis.rules.security.iam_002_sensitive_permission import SensitivePermissionRule

REVERT = object()   # annuler complètement le changement


@dataclass(frozen=True)
class Fix:
    change_id: str
    rule_id: str
    description: str
    after: Any            # nouvelle valeur `after`, ou REVERT
    origin: str = "catalog"

    @property
    def reverts(self) -> bool:
        return self.after is REVERT


# ------------------------------------------------------------------ réseau

def _vpc_cidr(ctx: AnalysisContext, sg_id: str) -> str | None:
    for rel in ctx.current_twin.relationships:
        if rel.type == "CONTAINS" and rel.target == sg_id:
            vpc = ctx.current_twin.get(rel.source)
            if vpc and vpc.type == "VPC":
                return vpc.properties.get("cidr") or vpc.properties.get("cidr_block")
    return None


def _restrict_ingress(rules: Any, vpc_cidr: str | None) -> Any:
    """Remplace 0.0.0.0/0 par le CIDR du VPC (ou retire la source), retire ::/0."""
    if not isinstance(rules, list):
        return None
    out = []
    for rule in copy.deepcopy(rules):
        if not isinstance(rule, dict):
            return None
        for key in ("cidr_blocks", "ipv6_cidr_blocks"):
            if isinstance(rule.get(key), list):
                kept = [c for c in rule[key] if c not in PUBLIC_CIDRS]
                if key == "cidr_blocks" and "0.0.0.0/0" in rule[key] and vpc_cidr and vpc_cidr not in kept:
                    kept.append(vpc_cidr)
                rule[key] = kept
        for key, field in (("IpRanges", "CidrIp"), ("Ipv6Ranges", "CidrIpv6")):
            if isinstance(rule.get(key), list):
                kept = [r for r in rule[key] if r.get(field) not in PUBLIC_CIDRS]
                if key == "IpRanges" and any(r.get(field) == "0.0.0.0/0" for r in rule[key]) and vpc_cidr:
                    kept.append({"CidrIp": vpc_cidr})
                rule[key] = kept
        sources = (rule.get("cidr_blocks") or []) + (rule.get("ipv6_cidr_blocks") or []) + \
                  (rule.get("IpRanges") or []) + (rule.get("Ipv6Ranges") or [])
        if sources or rule.get("security_groups") or rule.get("UserIdGroupPairs"):
            out.append(rule)
    return out


def _fix_security_group(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    if change.property_path not in ("properties.ingress", "properties.ingress_rules"):
        return None
    cidr = _vpc_cidr(ctx, change.resource_id)
    after = _restrict_ingress(change.after, cidr)
    if after is None:
        return None
    target = f"au réseau du VPC ({cidr})" if cidr else "en retirant les sources publiques"
    return after, f"Restreindre {change.resource_id} {target} au lieu de 0.0.0.0/0"


# ------------------------------------------------------------- résilience

def _fix_replicas(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    if change.property_path not in ("properties.replicas", "properties.spec.replicas"):
        return None
    target = max(2, change.before) if isinstance(change.before, int) else 2
    return target, f"Garder {target} réplicas pour {change.resource_id}"


def _fix_multi_az(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    if change.property_path not in ("properties.multi_az", "properties.MultiAZ"):
        return None
    return True, f"Garder Multi-AZ activé sur {change.resource_id}"


def _fix_delete(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    if change.operation != "delete":
        return None
    return REVERT, f"Ne pas supprimer {change.resource_id} tant que d'autres ressources en dépendent"


# ---------------------------------------------------------------- service

def _fix_service(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    path = change.property_path
    if path == "properties.service_type_and_scheme":
        return {"service_type": "ClusterIP"}, f"Garder {change.resource_id} en ClusterIP (interne)"
    if path == "properties.spec" and isinstance(change.after, dict):
        after = {k: v for k, v in change.after.items() if k != "loadBalancerSourceRanges"}
        return after | {"type": "ClusterIP"}, f"Garder {change.resource_id} en ClusterIP (interne)"
    if path == "properties.annotations" and isinstance(change.after, dict):
        key = "service.beta.kubernetes.io/aws-load-balancer-scheme"
        return change.after | {key: "internal"}, f"Passer le load balancer de {change.resource_id} en internal"
    return None


# ------------------------------------------------------------- Kubernetes

def _unprivilege(containers: Any) -> Any:
    """Les conteneurs sans `privileged: true` (formats manifest et découverte)."""
    out = copy.deepcopy(containers)
    for c in out if isinstance(out, list) else []:
        for key in ("securityContext", "security_context"):
            if isinstance(c, dict) and isinstance(c.get(key), dict) and c[key].get("privileged") is True:
                c[key]["privileged"] = False
    return out


def _fix_k8s(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    rid, path = change.resource_id, change.property_path
    if change.resource_type.upper() == "CLUSTER_ROLE_BINDING":
        return REVERT, f"Ne pas lier cluster-admin à un ServiceAccount ({rid}) : utiliser un Role limité"
    became_admin = (finding.evidence.get("cluster_admin") or {}).get("after") is True
    if path == "properties.service_account":
        return REVERT, f"Garder le ServiceAccount d'origine pour {rid}"
    if path in ("properties.containers", "properties.init_containers"):
        return _unprivilege(change.after), f"Retirer privileged: true des conteneurs de {rid}"
    if path == "properties.spec" and isinstance(change.after, dict):
        after = copy.deepcopy(change.after)
        pod = after.get("template", {}).get("spec") if isinstance(after.get("template"), dict) else after
        if not isinstance(pod, dict):
            return None
        for key in ("containers", "initContainers", "ephemeralContainers"):
            if key in pod:
                pod[key] = _unprivilege(pod[key])
        what = "Retirer privileged: true"
        old_sa = (finding.evidence.get("service_account") or {}).get("before")
        if became_admin:
            if old_sa:
                pod["serviceAccountName"] = old_sa.split(":", 1)[-1]
            else:
                pod.pop("serviceAccountName", None)
            what += " et garder un ServiceAccount sans cluster-admin"
        return after, f"{what} pour {rid}"
    return None


# -------------------------------------------------------------------- IAM

_ADMIN, _SENSITIVE = FullAdminPolicyRule(), SensitivePermissionRule()


def _dangerous(statement_doc: dict, label: str) -> bool:
    parsed = parse_policies([{"name": label, "document": {"Statement": [statement_doc]}}])
    if parsed is None:
        return False
    statements, _ = parsed
    return bool(_ADMIN.grants(statements) or _SENSITIVE.grants(statements))


def _clean_document(doc: dict, label: str) -> dict | None:
    """Le document sans ses déclarations dangereuses, ou None s'il ne reste rien."""
    raw = doc.get("Statement")
    statements = [raw] if isinstance(raw, dict) else list(raw or [])
    kept = [st for st in statements if not (isinstance(st, dict) and _dangerous(st, label))]
    return {**doc, "Statement": kept} if kept else None


def _clean_policy_item(item: Any) -> Any:
    """Un élément de liste de politiques nettoyé, ou None pour le retirer."""
    if isinstance(item, str):                                  # managed_policy_arns
        name = item.rsplit("/", 1)[-1]
        return None if ":iam::aws:policy/" in item and name in KNOWN_MANAGED else item
    if not isinstance(item, dict):
        return item
    label = item.get("arn") or item.get("name") or "?"
    if "document" not in item and "policy" not in item:       # politique gérée sans document
        name = str(item.get("arn", "")).rsplit("/", 1)[-1]
        return None if name in KNOWN_MANAGED else item
    raw = item.get("document", item.get("policy"))
    as_text = isinstance(raw, str)
    doc = json.loads(raw) if as_text else raw
    if not isinstance(doc, dict):
        return item
    cleaned = _clean_document(doc, label)
    if cleaned is None:
        return None
    key = "document" if "document" in item else "policy"
    return item | {key: json.dumps(cleaned) if as_text else cleaned}


def _fix_iam(change: Change, finding: Finding, ctx: AnalysisContext) -> tuple[Any, str] | None:
    if change.property_path not in ("properties.inline_policies", "properties.attached_policies",
                                    "properties.inline_policy", "properties.managed_policy_arns"):
        return None
    if not isinstance(change.after, list):
        return None
    try:
        after = [c for c in (_clean_policy_item(i) for i in change.after) if c is not None]
    except json.JSONDecodeError:
        return None
    if after == change.after:
        return None
    what = "les droits *:*" if finding.rule_id == "RULE-IAM-001" else "les permissions d'escalade (iam:PassRole…)"
    return after, f"Retirer {what} du rôle {change.resource_id}"


FIXERS: dict[str, Callable[[Change, Finding, AnalysisContext], tuple[Any, str] | None]] = {
    "RULE-NET-001": _fix_security_group,
    "RULE-NET-002": _fix_security_group,
    "RULE-NET-003": _fix_security_group,
    "RULE-NET-004": _fix_service,
    "RULE-RES-001": _fix_replicas,
    "RULE-RES-002": _fix_multi_az,
    "RULE-RES-003": _fix_delete,
    "RULE-IAM-001": _fix_iam,
    "RULE-IAM-002": _fix_iam,
    "RULE-K8S-001": _fix_k8s,
    # RULE-IAM-003 n'a pas de correctif propre : on coupe le chemin en corrigeant un maillon.
}


def propose(ctx: AnalysisContext, findings: list[Finding]) -> list[Fix]:
    """Un correctif par (changement, règle) en échec, sans doublon."""
    changes = {c.change_id: c for c in ctx.changes}
    fixes: dict[tuple[str, str], Fix] = {}
    for f in sorted(findings, key=lambda f: (f.rule_id, f.change_ids)):
        if f.status != "fail" or f.rule_id not in FIXERS or len(f.change_ids) != 1:
            continue
        change = changes.get(f.change_ids[0])
        if change is None:
            continue
        result = FIXERS[f.rule_id](change, f, ctx)
        if result is None:
            continue
        after, description = result
        # Une même correction réseau couvre NET-001/002/003 : une seule option.
        family = "sg" if f.rule_id.startswith("RULE-NET-00") and f.rule_id != "RULE-NET-004" else f.rule_id
        fixes.setdefault((change.change_id, family), Fix(change.change_id, f.rule_id, description, after))
    return list(fixes.values())
