"""RULE-NET-004 — Service Kubernetes exposé à Internet.

Un Service `ClusterIP` n'est joignable que depuis le cluster. Le passer en
`LoadBalancer` internet-facing crée un load balancer AWS public devant les
Pods : l'application devient joignable depuis Internet.

Logique, pour chaque changement d'un SERVICE qui touche son type, son schéma
ou ses annotations :
  - FAIL (high)    si le Service devient public alors qu'il ne l'était pas ;
  - FAIL (medium)  s'il reste public (exposition préexistante, jamais masquée) ;
  - PASS           s'il reste interne : ClusterIP, ExternalName, LoadBalancer
                   `internal`, ou LoadBalancer limité par loadBalancerSourceRanges ;
  - NOT_EVALUATED  si on ne peut pas conclure : type inconnu, NodePort (dépend
                   des Security Groups des nœuds), ou LoadBalancer sans schéma
                   connu (le défaut dépend du contrôleur installé).

Le schéma est lu dans le changement, puis dans les annotations du Service.
À défaut, le jumeau sert de preuve : si un load balancer internet-facing
route déjà vers ce Service, celui-ci est public.
"""

from __future__ import annotations

from typing import Any

from analysis.models import AnalysisContext, Category, Change, Finding, Severity, Status
from analysis.rules.base import Rule
from analysis.rules.security._network import PUBLIC_CIDRS, neighbours

SERVICE_TYPE = "SERVICE"
SCHEME_ANNOTATION = "service.beta.kubernetes.io/aws-load-balancer-scheme"
INTERNAL_ANNOTATION = "service.beta.kubernetes.io/aws-load-balancer-internal"
RANGES_ANNOTATION = "service.beta.kubernetes.io/load-balancer-source-ranges"

# Chemin du changement -> comment envelopper la valeur pour la lire comme un Service.
PATHS = {
    "properties": lambda v: v,
    "properties.spec": lambda v: v,
    "properties.service_type_and_scheme": lambda v: v,
    "properties.service_type": lambda v: {"service_type": v},
    "properties.scheme": lambda v: {"scheme": v},
    "properties.annotations": lambda v: {"annotations": v},
}

PUBLIC, PRIVATE, UNKNOWN = "public", "private", "unknown"


def _normalize(data: Any) -> dict[str, Any]:
    """Ramène les formats normalizer / manifest / scénario à 3 champs (None = inconnu)."""
    if not isinstance(data, dict):
        return {"type": None, "scheme": None, "source_ranges": None}
    ann = data.get("annotations") if isinstance(data.get("annotations"), dict) else {}

    scheme = data.get("scheme") or ann.get(SCHEME_ANNOTATION)
    if scheme is None and str(ann.get(INTERNAL_ANNOTATION, "")).lower() == "true":
        scheme = "internal"

    ranges = data.get("loadBalancerSourceRanges") or data.get("load_balancer_source_ranges")
    if ranges is None and isinstance(ann.get(RANGES_ANNOTATION), str):
        ranges = [r.strip() for r in ann[RANGES_ANNOTATION].split(",") if r.strip()]

    return {
        "type": data.get("service_type") or data.get("type"),
        "scheme": scheme,
        "source_ranges": ranges or None,
    }


def _overlay(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    return {k: top[k] if top[k] is not None else base[k] for k in base}


class PublicServiceRule(Rule):
    rule_id = "RULE-NET-004"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.HIGH
    title = "Service exposé à Internet"
    remediation = (
        "Garder le Service en ClusterIP et l'exposer via un Ingress/ALB contrôlé, "
        "ou utiliser un load balancer `internal` ; sinon limiter "
        "loadBalancerSourceRanges aux adresses autorisées."
    )
    reference = "Exposition potentielle associée à MITRE ATT&CK T1190"

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        for change in ctx.changes:
            if change.resource_type.upper() != SERVICE_TYPE or change.operation == "delete":
                continue
            wrap = PATHS.get(change.property_path or "properties")
            if wrap is None:
                continue

            rid = change.resource_id
            before_state, after_state = self._states(ctx, change, wrap)
            lb_public = self._public_lb_in_twin(ctx, rid)
            before, _ = self._classify(before_state, lb_public)
            after, reason = self._classify(after_state, lb_public)

            evidence = {
                "property_path": change.property_path,
                "before": before_state | {"exposure": before},
                "after": after_state | {"exposure": after},
                "public_load_balancer_in_twin": lb_public,
                "reason": reason,
            }
            ids = [change.change_id]

            if after == UNKNOWN:
                findings.append(self.finding(
                    Status.NOT_EVALUATED, f"Exposition future de {rid} indéterminée : {reason}.",
                    change_ids=ids, affected=[rid], evidence=evidence))
            elif after == PUBLIC and before != PUBLIC:
                findings.append(self.finding(
                    Status.FAIL, f"{rid} devient joignable depuis Internet ({reason}).",
                    change_ids=ids, affected=self._affected(ctx, change), evidence=evidence))
            elif after == PUBLIC:
                findings.append(self.finding(
                    Status.FAIL, f"{rid} était déjà joignable depuis Internet ({reason}).",
                    change_ids=ids, affected=self._affected(ctx, change), evidence=evidence,
                    severity=Severity.MEDIUM))
            else:
                findings.append(self.finding(
                    Status.PASS, f"{rid} reste interne ({reason}).",
                    change_ids=ids, affected=[rid], evidence=evidence))

        return findings

    # ------------------------------------------------------------------ outils

    @staticmethod
    def _states(ctx: AnalysisContext, change: Change, wrap) -> tuple[dict, dict]:
        current = ctx.current_twin.get(change.resource_id)
        base = _normalize(current.properties if current else None)
        if change.operation == "create":
            before = _normalize(None)
            after = _normalize(wrap(change.after))
        else:
            before = _overlay(base, _normalize(wrap(change.before)))
            after = _overlay(base, _normalize(wrap(change.after)))
        return before, after

    @staticmethod
    def _public_lb_in_twin(ctx: AnalysisContext, rid: str) -> bool:
        for rel in ctx.current_twin.relationships:
            if rel.target == rid and rel.type == "ROUTES_TO":
                lb = ctx.current_twin.get(rel.source)
                if lb and lb.type == "LOAD_BALANCER" and lb.properties.get("scheme") == "internet-facing":
                    return True
        return False

    @staticmethod
    def _classify(state: dict[str, Any], lb_public: bool) -> tuple[str, str]:
        stype = state["type"]
        if stype is None:
            return UNKNOWN, "type de Service inconnu"
        if stype in ("ClusterIP", "ExternalName"):
            return PRIVATE, f"type {stype}"
        if stype == "NodePort":
            return UNKNOWN, "NodePort : dépend des Security Groups des nœuds"
        if stype != "LoadBalancer":
            return UNKNOWN, f"type {stype!r} non reconnu"

        ranges = state["source_ranges"]
        if ranges and not PUBLIC_CIDRS.intersection(ranges):
            return PRIVATE, f"LoadBalancer limité à {', '.join(ranges)}"
        scheme = state["scheme"]
        if scheme == "internal":
            return PRIVATE, "LoadBalancer internal"
        if scheme == "internet-facing":
            return PUBLIC, "LoadBalancer internet-facing"
        if lb_public:
            return PUBLIC, "un load balancer internet-facing route vers ce Service dans le jumeau"
        return UNKNOWN, "LoadBalancer sans schéma connu (le défaut dépend du contrôleur)"

    @staticmethod
    def _affected(ctx: AnalysisContext, change: Change) -> list[str]:
        """Le Service, les workloads qu'il expose et ce qui route vers lui."""
        rid = change.resource_id
        out = [rid] + neighbours(ctx.current_twin, rid, {"EXPOSES", "ROUTES_TO"})
        out += [a.target for a in ctx.affected_resources
                if a.change_id == change.change_id and a.distance == 1]
        return out
