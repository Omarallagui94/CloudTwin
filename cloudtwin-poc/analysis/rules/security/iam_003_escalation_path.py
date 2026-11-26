"""RULE-IAM-003 — Nouveau chemin d'attaque Internet → rôle IAM dangereux.

C'est la règle que seul un graphe permet d'écrire. Prise seule, chaque
modification peut sembler anodine : un Service devient public, un rôle reçoit
`iam:PassRole`, un ServiceAccount change de rôle. Ensemble, elles peuvent
créer un chemin complet :

    Internet → LoadBalancer → Service → Pod → ServiceAccount → (IRSA) → rôle IAM

ou, si le Pod n'a pas de rôle IRSA, via le rôle du nœud EKS :

    … → Pod → Node → EC2 → NodeGroup → rôle IAM          (accès à l'IMDS)

Un rôle est « dangereux » s'il déclenche RULE-IAM-001 (*:*) ou RULE-IAM-002
(permission d'escalade). La règle construit le graphe avant et après le
changement, puis compare les chemins depuis Internet.

  - FAIL (critical)  si un nouveau chemin mène à un rôle dangereux via IRSA ;
  - FAIL (high)      si le nouveau chemin passe seulement par le rôle du nœud
                     (il suppose que l'IMDS est joignable depuis le Pod) ;
  - FAIL (medium)    si le chemin existait déjà ;
  - PASS             s'il n'existe aucun chemin ;
  - NOT_EVALUATED    si une donnée nécessaire est inconnue (exposition d'un
                     Service, contenu d'une politique) et qu'aucun chemin certain
                     n'est trouvé.

La règle ne produit un finding que si la simulation touche un Service, un
ServiceAccount ou un rôle IAM. Limite de la v1 : l'exposition par un
Security Group (nœuds en sous-réseau public) n'est pas encore modélisée.

Référence vérifiée : escalade de privilèges potentielle associée à
MITRE ATT&CK T1098.003 ; exposition associée à T1190.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from analysis.models import AnalysisContext, Category, Change, Finding, Severity, Status
from analysis.rules.base import Rule
from analysis.rules.security._iam import POLICY_KEYS, POLICY_PATHS, parse_policies
from analysis.rules.security.iam_001_full_admin import FullAdminPolicyRule
from analysis.rules.security.iam_002_sensitive_permission import SensitivePermissionRule
from analysis.rules.security.net_004_public_service import PATHS as SERVICE_PATHS
from analysis.rules.security.net_004_public_service import PUBLIC, PRIVATE, PublicServiceRule

INTERNET = "aws:internet"
IRSA_ANNOTATION = "eks.amazonaws.com/role-arn"

# Arêtes du jumeau suivies dans le sens de l'attaque : (type, type source, type cible).
ATTACK_EDGES = {
    ("EXPOSES", "INTERNET", "LOAD_BALANCER"),
    ("ROUTES_TO", "LOAD_BALANCER", "SERVICE"),
    ("ROUTES_TO", "INGRESS", "SERVICE"),
    ("EXPOSES", "SERVICE", None),           # Service -> Deployment / StatefulSet
    ("SELECTS", "SERVICE", "POD"),
    ("SELECTS", None, "POD"),               # workload -> Pod
    ("USES", None, "SERVICE_ACCOUNT"),
    ("RUNS_ON", "POD", "NODE"),
    ("RUNS_ON", "NODE", "EC2_INSTANCE"),
    ("MEMBER_OF", "EC2_INSTANCE", "EKS_NODE_GROUP"),
    ("USES", "EKS_NODE_GROUP", "IAM_ROLE"),
}
RELEVANT_TYPES = {"SERVICE", "SERVICE_ACCOUNT", "IAM_ROLE"}

_ADMIN = FullAdminPolicyRule()
_SENSITIVE = SensitivePermissionRule()


@dataclass
class _State:
    graph: nx.DiGraph
    dangerous: dict[str, list[str]] = field(default_factory=dict)   # rôle -> raisons
    unknown: list[str] = field(default_factory=list)                 # données inconnues


class EscalationPathRule(Rule):
    rule_id = "RULE-IAM-003"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.CRITICAL
    title = "Nouveau chemin d'escalade depuis Internet"
    remediation = ("Couper le chemin au maillon le moins coûteux : garder le Service interne, "
                   "retirer la permission dangereuse du rôle, ou donner au ServiceAccount un "
                   "rôle IRSA minimal (et bloquer l'IMDS des Pods : hop limit = 1).")
    reference = ("Escalade de privilèges potentielle associée à MITRE ATT&CK T1098.003 ; "
                 "exposition associée à T1190")

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        relevant = [c for c in ctx.changes if c.resource_type.upper() in RELEVANT_TYPES]
        if not relevant:
            return []
        ids = [c.change_id for c in relevant]

        before = self._build(ctx, apply_changes=False)
        after = self._build(ctx, apply_changes=True)
        paths_before = self._paths(before)
        paths_after = self._paths(after)

        newly = {k: p for k, p in paths_after.items() if k not in paths_before}
        already = {k: p for k, p in paths_after.items() if k in paths_before}
        evidence = {
            "new_paths": [self._describe_path(after, k, p) for k, p in sorted(newly.items())],
            "existing_paths": [self._describe_path(after, k, p) for k, p in sorted(already.items())],
            "unknown": sorted(set(after.unknown)),
        }

        if newly:
            via_irsa = any(via == "IRSA" for (_, via) in newly)
            affected = sorted({n for p in newly.values() for n in p if n != INTERNET})
            roles = ", ".join(sorted({r for (r, _) in newly}))
            return [self.finding(
                Status.FAIL,
                f"Nouveau chemin depuis Internet jusqu'au rôle {roles} "
                f"({'IRSA' if via_irsa else 'rôle du nœud via IMDS'}).",
                change_ids=ids, affected=affected, evidence=evidence,
                severity=Severity.CRITICAL if via_irsa else Severity.HIGH)]
        if already:
            affected = sorted({n for p in already.values() for n in p if n != INTERNET})
            return [self.finding(
                Status.FAIL, "Un chemin Internet → rôle IAM dangereux existait déjà et reste ouvert.",
                change_ids=ids, affected=affected, evidence=evidence, severity=Severity.MEDIUM)]
        if after.unknown:
            return [self.finding(
                Status.NOT_EVALUATED,
                f"Chemin d'escalade indéterminé : données inconnues ({', '.join(sorted(set(after.unknown)))}).",
                change_ids=ids, affected=[c.resource_id for c in relevant], evidence=evidence)]
        return [self.finding(
            Status.PASS, "Aucun chemin depuis Internet vers un rôle IAM dangereux.",
            change_ids=ids, affected=[c.resource_id for c in relevant], evidence=evidence)]

    # ------------------------------------------------------- construction du graphe

    def _build(self, ctx: AnalysisContext, *, apply_changes: bool) -> _State:
        twin = ctx.current_twin
        types = {r.id: r.type for r in twin.resources}
        props = {r.id: dict(r.properties) for r in twin.resources}
        changes = ctx.changes if apply_changes else []

        # 1. Propriétés futures des ServiceAccounts et des rôles.
        for c in changes:
            ctype = c.resource_type.upper()
            if ctype not in ("SERVICE_ACCOUNT", "IAM_ROLE") or c.operation == "delete":
                continue
            types.setdefault(c.resource_id, ctype)
            if c.operation == "create" and isinstance(c.after, dict):
                props[c.resource_id] = dict(c.after)
            elif c.property_path and c.property_path.startswith("properties."):
                props.setdefault(c.resource_id, {})[c.property_path.split(".", 1)[1]] = c.after

        g = nx.DiGraph()
        g.add_node(INTERNET)
        for rel in twin.relationships:
            if self._is_attack_edge(rel.type, types.get(rel.source), types.get(rel.target)):
                g.add_edge(rel.source, rel.target, via=rel.type)

        state = _State(graph=g)

        # 2. Exposition future des Services modifiés.
        for c in changes:
            if c.resource_type.upper() != "SERVICE" or c.operation == "delete":
                continue
            wrap = SERVICE_PATHS.get(c.property_path or "properties")
            if wrap is None:
                continue
            _, after_state = PublicServiceRule._states(ctx, c, wrap)
            lb_public = PublicServiceRule._public_lb_in_twin(ctx, c.resource_id)
            exposure, _ = PublicServiceRule._classify(after_state, lb_public)
            if exposure == PUBLIC and not nx.has_path(g, INTERNET, c.resource_id):
                g.add_edge(INTERNET, c.resource_id, via="EXPOSES (simulé)")
            elif exposure == PRIVATE:
                for src in list(g.predecessors(c.resource_id)):
                    if types.get(src) == "LOAD_BALANCER":
                        g.remove_edge(src, c.resource_id)
            elif exposure not in (PUBLIC, PRIVATE):
                state.unknown.append(f"exposition de {c.resource_id}")

        # 3. Liens IRSA : ServiceAccount -> rôle, d'après l'annotation.
        role_by_arn = {p.get("arn"): rid for rid, p in props.items()
                       if types.get(rid) == "IAM_ROLE" and p.get("arn")}
        for rid, p in props.items():
            if types.get(rid) != "SERVICE_ACCOUNT":
                continue
            ann = p.get("annotations") if isinstance(p.get("annotations"), dict) else {}
            arn = ann.get(IRSA_ANNOTATION)
            if not arn:
                continue
            role_id = role_by_arn.get(arn, f"aws:iam-role:{arn}")
            types.setdefault(role_id, "IAM_ROLE")
            g.add_edge(rid, role_id, via="IRSA")

        # 4. Rôles dangereux.
        for rid, rtype in types.items():
            if rtype != "IAM_ROLE":
                continue
            reasons, unknown = self._danger(props.get(rid))
            if reasons:
                state.dangerous[rid] = reasons
            elif unknown and rid in g and nx.has_path(g, INTERNET, rid):
                state.unknown.append(f"politiques de {rid}")
        return state

    @staticmethod
    def _is_attack_edge(rel_type: str, src_type: str | None, dst_type: str | None) -> bool:
        return any(t == rel_type and (s is None or s == src_type) and (d is None or d == dst_type)
                   for t, s, d in ATTACK_EDGES)

    @staticmethod
    def _danger(role_props: dict[str, Any] | None) -> tuple[list[str], bool]:
        """(raisons du danger, contenu partiellement inconnu)."""
        # Aucune clé de politique = la collecte ne les a pas lues : inconnu, pas « vide ».
        if not role_props or not any(k in role_props for k in POLICY_KEYS):
            return [], True
        collection_incomplete = any(
            role_props.get(key) is False
            for key in ("attached_policies_complete", "inline_policies_complete")
        )
        items: list = []
        for key in POLICY_KEYS:
            value = role_props.get(key)
            if value is None:
                continue
            if not isinstance(value, list):
                return [], True
            items += value
        parsed = parse_policies(items)
        if parsed is None:
            return [], True
        statements, unknown = parsed
        reasons = [f"*:* via {d['policy']}" for d in _ADMIN.grants(statements).values()]
        reasons += sorted({f"{d['action']} via {d['policy']}" for d in _SENSITIVE.grants(statements).values()})
        return reasons, bool(unknown) or collection_incomplete

    # ---------------------------------------------------------------- chemins

    @staticmethod
    def _paths(state: _State) -> dict[tuple[str, str], list[str]]:
        """{(rôle, IRSA|IMDS): plus court chemin depuis Internet}."""
        g = state.graph
        out: dict[tuple[str, str], list[str]] = {}
        for role in state.dangerous:
            if role not in g:
                continue
            for pred in g.predecessors(role):
                via = "IRSA" if g.edges[pred, role]["via"] == "IRSA" else "IMDS"
                if (role, via) in out or not nx.has_path(g, INTERNET, pred):
                    continue
                out[(role, via)] = nx.shortest_path(g, INTERNET, pred) + [role]
        return out

    @staticmethod
    def _describe_path(state: _State, key: tuple[str, str], path: list[str]) -> dict[str, Any]:
        role, via = key
        g = state.graph
        return {
            "role": role,
            "via": via,
            "path": path,
            "edges": [g.edges[a, b]["via"] for a, b in zip(path, path[1:])],
            "role_permissions": state.dangerous[role],
        }
