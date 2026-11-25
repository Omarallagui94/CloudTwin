"""RULE-K8S-001 — Workload privilégié ou ServiceAccount lié à cluster-admin.

Deux façons, dans Kubernetes, de donner à un Pod les pleins pouvoirs :

  1. un conteneur `privileged: true` voit tous les périphériques et toutes les
     capacités du nœud : il peut sortir du conteneur et prendre la main sur
     l'hôte ;
  2. un ServiceAccount lié au ClusterRole `cluster-admin` par un
     ClusterRoleBinding peut tout faire dans tout le cluster, et chaque Pod qui
     l'utilise hérite de ce pouvoir.

La règle ne regarde que les changements qui peuvent modifier ces privilèges
(spec du Pod, conteneurs, ServiceAccount, création, remplacement, binding) :
un changement de réplicas ne la déclenche pas.

Elle compare l'état actuel et l'état futur de chaque ressource modifiée :

  Workload (Deployment, StatefulSet, DaemonSet, Pod, Job, CronJob)
    - FAIL (high)    s'il devient privilégié ou passe sur un ServiceAccount
                     cluster-admin alors qu'il ne l'était pas ;
    - FAIL (medium)  s'il l'était déjà et le reste (risque préexistant, jamais masqué) ;
    - PASS           si aucun conteneur n'est privilégié et que son
                     ServiceAccount n'est pas cluster-admin ;
    - NOT_EVALUATED  si la liste des conteneurs est inconnue (jumeau collecté
                     avant l'enrichissement de la découverte).

  ClusterRoleBinding
    - FAIL (high)    s'il lie `cluster-admin` à un ServiceAccount qui ne l'avait
                     pas ; les workloads qui utilisent ce ServiceAccount sont
                     marqués impactés ;
    - FAIL (medium)  si ce lien existait déjà ;
    - PASS           s'il vise un autre rôle, ou seulement des utilisateurs et groupes ;
    - NOT_EVALUATED  si son rôle ou ses sujets sont inconnus.

Les deux formats du jumeau sont lus : la découverte d'Omar
(`containers[].security_context.privileged`, `role_ref`, `subjects`) et un
manifest Kubernetes (`spec.template.spec.containers[].securityContext`,
`roleRef`). Dans Kubernetes, un conteneur sans `securityContext` n'est pas
privilégié : c'est la valeur par défaut, pas une donnée manquante.

Limite : les RoleBindings (liens dans un seul namespace) ne sont pas collectés.
"""

from __future__ import annotations

import copy
from typing import Any

from analysis.models import AnalysisContext, Category, Finding, Severity, Status, Twin
from analysis.rules.base import Rule

WORKLOAD_TYPES = {"DEPLOYMENT", "STATEFUL_SET", "DAEMON_SET", "POD", "JOB", "CRON_JOB", "REPLICA_SET"}
BINDING_TYPE = "CLUSTER_ROLE_BINDING"
ADMIN_ROLE = "cluster-admin"
CONTAINER_KEYS = ("containers", "initContainers", "ephemeralContainers")
NORMALIZED_CONTAINER_KEYS = ("containers", "init_containers")

UNKNOWN = None   # « on ne sait pas », distinct de False

# Propriétés d'un workload qui peuvent changer ses privilèges. Un changement de
# réplicas ne les touche pas : la règle ne s'applique pas.
WORKLOAD_PATH_PREFIXES = ("properties.spec", "properties.containers", "properties.init_containers",
                          "properties.service_account", "properties.serviceAccountName")


def _relevant(change) -> bool:
    rtype = change.resource_type.upper()
    if change.operation == "delete":
        return False
    if rtype == BINDING_TYPE:
        return True
    if rtype not in WORKLOAD_TYPES:
        return False
    path = change.property_path
    return (change.operation in ("create", "replace") or path in (None, "properties")
            or any(path == p or path.startswith(p + ".") or path.startswith(p + "[")
                   for p in WORKLOAD_PATH_PREFIXES))


# ------------------------------------------------------------ lecture des formats

def _pod_spec(props: dict[str, Any]) -> dict[str, Any] | None:
    """Le spec de Pod d'un manifest (Pod, Deployment/StatefulSet/Job, CronJob).

    Accepte le manifest entier (`spec` dedans) ou seulement son `spec`, tel qu'un
    changement `replace` le transmet. None si aucun spec de Pod n'est trouvé.
    """
    roots = [props["spec"]] if isinstance(props.get("spec"), dict) else []
    if "template" in props or "jobTemplate" in props:
        roots.append(props)
    for root in roots:
        for path in (("jobTemplate", "spec", "template", "spec"), ("template", "spec"), ()):
            node: Any = root
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, dict) and any(k in node for k in CONTAINER_KEYS):
                return node
    return None


def _is_privileged(container: Any) -> bool:
    if not isinstance(container, dict):
        return False
    context = container.get("securityContext") or container.get("security_context")
    return isinstance(context, dict) and context.get("privileged") is True


def _privileged_containers(props: dict[str, Any]) -> list[str] | None:
    """Noms des conteneurs privilégiés ; None si la liste des conteneurs est inconnue."""
    pod_spec = _pod_spec(props)
    if pod_spec is not None:
        containers = [c for key in CONTAINER_KEYS for c in pod_spec.get(key) or []]
    elif any(key in props for key in NORMALIZED_CONTAINER_KEYS):
        # Format de la découverte, ou spec de Pod transmis seul (mêmes clés).
        containers = [c for key in (*NORMALIZED_CONTAINER_KEYS, *CONTAINER_KEYS[1:])
                      for c in props.get(key) or []]
    else:
        return UNKNOWN
    return sorted(str(c.get("name", "?")) for c in containers if _is_privileged(c))


def _namespace(resource_id: str, props: dict[str, Any]) -> str:
    metadata = props.get("metadata") if isinstance(props.get("metadata"), dict) else {}
    ns = props.get("namespace") or metadata.get("namespace")
    if ns:
        return str(ns)
    parts = resource_id.split(":")
    return parts[2] if len(parts) >= 4 else "default"


def _service_account(resource_id: str, props: dict[str, Any]) -> str | None:
    """ServiceAccount utilisé, au format « namespace:nom » ; None si inconnu."""
    ns = _namespace(resource_id, props)
    pod_spec = _pod_spec(props)
    if pod_spec is not None:
        # Sans serviceAccountName, Kubernetes utilise le ServiceAccount « default ».
        name = pod_spec.get("serviceAccountName") or pod_spec.get("serviceAccount") or "default"
        return f"{ns}:{name}"
    name = props.get("service_account") or props.get("serviceAccountName")
    return f"{ns}:{name}" if name else None


def _binding(props: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]] | None]:
    """(nom du rôle, sujets) d'un ClusterRoleBinding ; None = inconnu."""
    ref = props.get("role_ref") or props.get("roleRef")
    role = ref.get("name") if isinstance(ref, dict) else None
    subjects = props.get("subjects")
    if subjects is None and role is None:
        return None, None
    return role, [s for s in subjects or [] if isinstance(s, dict)]


def _admin_service_accounts(role: str | None, subjects: list[dict[str, Any]] | None) -> set[str]:
    if role != ADMIN_ROLE:
        return set()
    return {f"{s.get('namespace') or 'default'}:{s.get('name')}"
            for s in subjects or [] if s.get("kind") == "ServiceAccount" and s.get("name")}


# --------------------------------------------------------------- états du jumeau

def _props_by_id(twin: Twin) -> dict[str, tuple[str, dict[str, Any]]]:
    return {r.id: (r.type.upper(), r.properties) for r in twin.resources}


def _apply(current: Twin, ctx: AnalysisContext) -> dict[str, tuple[str, dict[str, Any]]]:
    """État futur : les changements rejoués sur une copie des propriétés.

    Comme IAM-003, la règle reconstruit l'état futur à partir des changements au
    lieu de lire ctx.future_twin : un correctif rejoué (remediation/verify.py)
    ne modifie que les changements, et la règle doit le voir.
    """
    state = {rid: (t, copy.deepcopy(p)) for rid, (t, p) in _props_by_id(current).items()}
    for change in sorted(ctx.changes, key=lambda c: c.change_id):
        rid, rtype = change.resource_id, change.resource_type.upper()
        if change.operation == "delete":
            state.pop(rid, None)
            continue
        if change.operation == "create" or change.property_path in (None, "properties") \
                or change.operation == "replace":
            state[rid] = (rtype, copy.deepcopy(change.after) if isinstance(change.after, dict) else {})
            continue
        if rid not in state:
            continue
        keys = change.property_path.split(".")[1:]
        node = state[rid][1]
        for key in keys[:-1]:
            node = node.setdefault(key, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict) and keys:
            node[keys[-1]] = copy.deepcopy(change.after)
    return state


def _all_admin_service_accounts(state: dict[str, tuple[str, dict[str, Any]]], twin: Twin | None) -> set[str]:
    """ServiceAccounts liés à cluster-admin, par les propriétés et par les relations du jumeau."""
    out: set[str] = set()
    for rtype, props in state.values():
        if rtype == BINDING_TYPE:
            out |= _admin_service_accounts(*_binding(props))
    if twin is not None:
        # Les relations ne servent que si les propriétés du binding ne disent rien :
        # un binding modifié garde ses anciennes relations dans le jumeau.
        admin_bindings = {r.source for r in twin.relationships
                          if r.type == "USES" and r.target == f"k8s:cluster-role:{ADMIN_ROLE}"
                          and r.source in state and _binding(state[r.source][1])[0] is None}
        prefix = "k8s:service-account:"
        out |= {r.target[len(prefix):] for r in twin.relationships
                if r.type == "USES" and r.source in admin_bindings and r.target.startswith(prefix)}
    return out


class PrivilegedWorkloadRule(Rule):
    rule_id = "RULE-K8S-001"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.HIGH
    title = "Workload privilégié ou lié à cluster-admin"
    remediation = (
        "Retirer `privileged: true` (ajouter seulement les capacités Linux nécessaires) ; "
        "donner au ServiceAccount un Role limité à son namespace au lieu de cluster-admin, "
        "et bloquer les conteneurs privilégiés par une politique d'admission (Pod Security « restricted »)."
    )
    reference = ("CIS Kubernetes Benchmark v1.10 5.1.1 et 5.2.2 ; sortie potentielle "
                 "vers le nœud associée à MITRE ATT&CK T1611")

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        relevant = [c for c in ctx.changes if _relevant(c)]
        if not relevant:
            return []

        before = _props_by_id(ctx.current_twin)
        after = _apply(ctx.current_twin, ctx)
        admins_before = _all_admin_service_accounts(before, ctx.current_twin)
        admins_after = _all_admin_service_accounts(after, ctx.current_twin)

        by_resource: dict[str, list[str]] = {}
        for change in relevant:
            by_resource.setdefault(change.resource_id, []).append(change.change_id)

        findings: list[Finding] = []
        for rid, change_ids in sorted(by_resource.items()):
            if rid not in after:
                continue
            rtype, props = after[rid]
            if rtype == BINDING_TYPE:
                findings.append(self._binding_finding(ctx, rid, change_ids, before, props, after))
            else:
                findings.append(self._workload_finding(
                    rid, change_ids, before.get(rid), props, admins_before, admins_after))
        return findings

    # ---------------------------------------------------------------- workloads

    def _workload_finding(self, rid, change_ids, before_entry, props, admins_before, admins_after) -> Finding:
        priv_after = _privileged_containers(props)
        sa_after = _service_account(rid, props)
        admin_after = sa_after in admins_after if sa_after else False

        before_props = before_entry[1] if before_entry else {}
        priv_before = _privileged_containers(before_props) if before_entry else []
        sa_before = _service_account(rid, before_props) if before_entry else None
        admin_before = sa_before in admins_before if sa_before else False

        reasons = []
        if priv_after:
            reasons.append(f"conteneur(s) privilégié(s) : {', '.join(priv_after)}")
        if admin_after:
            reasons.append(f"ServiceAccount {sa_after} lié à {ADMIN_ROLE}")
        evidence = {
            "privileged_containers": {"before": priv_before, "after": priv_after},
            "service_account": {"before": sa_before, "after": sa_after},
            "cluster_admin": {"before": admin_before, "after": admin_after},
        }
        affected = [rid] + ([f"k8s:service-account:{sa_after}"] if admin_after else [])

        if reasons:
            was_risky = bool(priv_before) or admin_before
            if was_risky:
                return self.finding(
                    Status.FAIL, f"{rid} avait déjà les pleins pouvoirs et les garde ({' ; '.join(reasons)}).",
                    change_ids=change_ids, affected=affected, evidence=evidence, severity=Severity.MEDIUM)
            return self.finding(
                Status.FAIL, f"{rid} obtient les pleins pouvoirs ({' ; '.join(reasons)}).",
                change_ids=change_ids, affected=affected, evidence=evidence)
        if priv_after is UNKNOWN:
            return self.finding(
                Status.NOT_EVALUATED,
                f"Conteneurs de {rid} inconnus : relancer la découverte enrichie (securityContext).",
                change_ids=change_ids, affected=[rid], evidence=evidence)
        return self.finding(
            Status.PASS, f"{rid} : aucun conteneur privilégié, ServiceAccount sans {ADMIN_ROLE}.",
            change_ids=change_ids, affected=[rid], evidence=evidence)

    # ----------------------------------------------------------------- bindings

    def _binding_finding(self, ctx, rid, change_ids, before, props, after) -> Finding:
        role, subjects = _binding(props)
        old_role, old_subjects = _binding(before[rid][1]) if rid in before else (None, [])
        evidence = {"role": role, "subjects": subjects,
                    "before": {"role": old_role, "subjects": old_subjects}}
        if role is None or subjects is None:
            return self.finding(
                Status.NOT_EVALUATED, f"Rôle ou sujets de {rid} inconnus.",
                change_ids=change_ids, affected=[rid], evidence=evidence)

        admins = _admin_service_accounts(role, subjects)
        if not admins:
            return self.finding(
                Status.PASS,
                f"{rid} ne lie aucun ServiceAccount à {ADMIN_ROLE} (rôle {role}).",
                change_ids=change_ids, affected=[rid], evidence=evidence)

        new = admins - _admin_service_accounts(old_role, old_subjects)
        users = self._workloads_using(after, admins)
        affected = [rid] + [f"k8s:service-account:{sa}" for sa in admins] + users
        evidence["workloads"] = users
        if new:
            return self.finding(
                Status.FAIL,
                f"{rid} donne {ADMIN_ROLE} au(x) ServiceAccount(s) {', '.join(sorted(new))}"
                + (f", utilisé(s) par {len(users)} workload(s)." if users else "."),
                change_ids=change_ids, affected=affected, evidence=evidence)
        return self.finding(
            Status.FAIL,
            f"{rid} liait déjà {ADMIN_ROLE} à {', '.join(sorted(admins))} et le garde.",
            change_ids=change_ids, affected=affected, evidence=evidence, severity=Severity.MEDIUM)

    @staticmethod
    def _workloads_using(state, service_accounts: set[str]) -> list[str]:
        return sorted(rid for rid, (rtype, props) in state.items()
                      if rtype in WORKLOAD_TYPES and _service_account(rid, props) in service_accounts)
