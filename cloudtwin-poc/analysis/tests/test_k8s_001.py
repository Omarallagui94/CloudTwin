"""Tests de RULE-K8S-001 (conteneur privilégié ou ServiceAccount lié à cluster-admin).

Le jumeau du PoC date d'avant l'enrichissement de la découverte : ses workloads
n'ont pas de liste de conteneurs. On l'enrichit ici au format produit par la
découverte d'Omar (containers[].security_context, ClusterRoleBinding avec
role_ref et subjects).
"""

import copy

from analysis.engine import run
from analysis.models import Severity, Status
from analysis.rules.security.k8s_001_privileged_workload import PrivilegedWorkloadRule
from analysis.tests.test_network_rules import REAL_TWIN, ctx
from simulation.pipeline import simulate

rule = PrivilegedWorkloadRule()

DEPLOY_UI = "k8s:deployment:default:ui"
DEPLOY_CATALOG = "k8s:deployment:default:catalog"
POD_CATALOG = "k8s:pod:default:catalog-5c8b-fghij"
SA_CATALOG = "k8s:service-account:default:catalog"
BINDING = "k8s:cluster-role-binding:catalog-admin"


def container(name="app", privileged=None):
    context = None if privileged is None else {"privileged": privileged}
    return {"name": name, "image": f"{name}:1.0", "init": False, "security_context": context}


def enriched_twin(*, ui_privileged=None, bindings=()):
    """Jumeau réel + conteneurs (format découverte) + ClusterRoleBindings."""
    twin = copy.deepcopy(REAL_TWIN)
    for r in twin["resources"]:
        if r["type"] in ("DEPLOYMENT", "POD"):
            privileged = ui_privileged if r["id"] == DEPLOY_UI else None
            r["properties"]["containers"] = [container(privileged=privileged)]
            r["properties"]["init_containers"] = []
    for name, role, subjects in bindings:
        twin["resources"].append({
            "id": f"k8s:cluster-role-binding:{name}", "name": name,
            "type": "CLUSTER_ROLE_BINDING", "provider": "kubernetes",
            "properties": {"name": name, "role_ref": {"apiGroup": "rbac.authorization.k8s.io",
                                                       "kind": "ClusterRole", "name": role},
                           "subjects": subjects}})
    return twin


def sa(name, namespace="default"):
    return {"kind": "ServiceAccount", "name": name, "namespace": namespace}


def deployment_spec(privileged=False, service_account=None):
    pod = {"containers": [{"name": "app", "image": "app:1.0",
                           **({"securityContext": {"privileged": True}} if privileged else {})}]}
    if service_account:
        pod["serviceAccountName"] = service_account
    return {"replicas": 3, "template": {"spec": pod}}


def spec_change(resource_id, spec, before=None):
    return {"operation": "update", "resource_id": resource_id, "resource_type": "DEPLOYMENT",
            "provider": "kubernetes", "property_path": "properties.spec",
            "before": before, "after": spec}


def binding_create(role, subjects, name="catalog-admin"):
    return {"operation": "create", "resource_id": f"k8s:cluster-role-binding:{name}",
            "resource_type": "CLUSTER_ROLE_BINDING", "provider": "kubernetes",
            "property_path": "properties", "before": None,
            "after": {"kind": "ClusterRoleBinding", "metadata": {"name": name},
                      "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                                  "name": role},
                      "subjects": subjects}}


# --------------------------------------------------------- conteneur privilégié

def test_container_becoming_privileged_is_high():
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, deployment_spec(privileged=True)),
                            twin=enriched_twin()))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
    assert f.evidence["privileged_containers"] == {"before": [], "after": ["app"]}
    assert "pleins pouvoirs" in f.description


def test_already_privileged_container_is_medium():
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, deployment_spec(privileged=True)),
                            twin=enriched_twin(ui_privileged=True)))
    assert f.status == Status.FAIL and f.severity == Severity.MEDIUM


def test_removing_privileged_passes():
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, deployment_spec(privileged=False)),
                            twin=enriched_twin(ui_privileged=True)))
    assert f.status == Status.PASS


def test_no_security_context_means_not_privileged():
    """Sans securityContext, Kubernetes ne rend pas le conteneur privilégié."""
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, deployment_spec()), twin=enriched_twin()))
    assert f.status == Status.PASS


def test_replica_change_does_not_trigger_the_rule():
    change = {"operation": "update", "resource_id": DEPLOY_UI, "resource_type": "DEPLOYMENT",
              "provider": "kubernetes", "property_path": "properties.replicas",
              "before": 3, "after": 1}
    assert rule.evaluate(ctx(change)) == []


def test_unknown_containers_are_not_evaluated_never_pass():
    """Jumeau collecté avant l'enrichissement : on ne conclut pas."""
    change = {"operation": "update", "resource_id": DEPLOY_UI, "resource_type": "DEPLOYMENT",
              "provider": "kubernetes", "property_path": "properties.service_account",
              "before": "ui", "after": "catalog"}
    [f] = rule.evaluate(ctx(change))
    assert f.status == Status.NOT_EVALUATED


def test_privileged_init_container_counts():
    spec = {"template": {"spec": {
        "initContainers": [{"name": "setup", "securityContext": {"privileged": True}}],
        "containers": [{"name": "app"}]}}}
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, spec), twin=enriched_twin()))
    assert f.status == Status.FAIL and f.evidence["privileged_containers"]["after"] == ["setup"]


def test_privileged_in_discovery_format():
    change = {"operation": "update", "resource_id": DEPLOY_UI, "resource_type": "DEPLOYMENT",
              "provider": "kubernetes", "property_path": "properties.containers",
              "before": [container()], "after": [container(privileged=True)]}
    [f] = rule.evaluate(ctx(change, twin=enriched_twin()))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH


# ------------------------------------------------------------- cluster-admin

def test_binding_cluster_admin_to_service_account_is_high():
    [f] = rule.evaluate(ctx(binding_create("cluster-admin", [sa("catalog")]), twin=enriched_twin()))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
    # Les workloads qui utilisent ce ServiceAccount héritent des pleins pouvoirs.
    assert {BINDING, SA_CATALOG, DEPLOY_CATALOG, POD_CATALOG} <= set(f.affected_resource_ids)
    assert DEPLOY_UI not in f.affected_resource_ids


def test_binding_to_another_role_passes():
    [f] = rule.evaluate(ctx(binding_create("view", [sa("catalog")]), twin=enriched_twin()))
    assert f.status == Status.PASS


def test_cluster_admin_for_users_only_passes():
    """La règle vise les ServiceAccounts, c'est-à-dire les Pods."""
    users = [{"kind": "Group", "name": "platform-admins"}]
    [f] = rule.evaluate(ctx(binding_create("cluster-admin", users), twin=enriched_twin()))
    assert f.status == Status.PASS


def test_existing_cluster_admin_binding_is_medium():
    twin = enriched_twin(bindings=[("catalog-admin", "cluster-admin", [sa("catalog")])])
    [f] = rule.evaluate(ctx(binding_create("cluster-admin", [sa("catalog")]), twin=twin))
    assert f.status == Status.FAIL and f.severity == Severity.MEDIUM


def test_workload_moving_to_admin_service_account_is_high():
    twin = enriched_twin(bindings=[("ops-admin", "cluster-admin", [sa("ops")])])
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, deployment_spec(service_account="ops")), twin=twin))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
    assert f.evidence["cluster_admin"] == {"before": False, "after": True}
    assert "k8s:service-account:default:ops" in f.affected_resource_ids


def test_binding_read_from_twin_relationships():
    """Binding sans role_ref dans ses propriétés : les relations du normalizer suffisent."""
    twin = enriched_twin()
    twin["resources"].append({"id": "k8s:cluster-role-binding:legacy", "name": "legacy",
                              "type": "CLUSTER_ROLE_BINDING", "provider": "kubernetes",
                              "properties": {"name": "legacy"}})
    twin["relationships"] += [
        {"source": "k8s:cluster-role-binding:legacy", "target": "k8s:cluster-role:cluster-admin",
         "type": "USES"},
        {"source": "k8s:cluster-role-binding:legacy", "target": "k8s:service-account:default:ops",
         "type": "USES"},
    ]
    [f] = rule.evaluate(ctx(spec_change(DEPLOY_UI, deployment_spec(service_account="ops")), twin=twin))
    assert f.status == Status.FAIL and f.evidence["cluster_admin"]["after"] is True


def test_unrelated_changes_are_ignored():
    sg = {"operation": "update", "resource_id": "aws:security-group:sg-0node",
          "resource_type": "SECURITY_GROUP", "provider": "aws",
          "property_path": "properties.ingress", "before": [], "after": []}
    assert rule.evaluate(ctx(sg, twin=enriched_twin())) == []


def test_deletion_is_ignored():
    delete = {"operation": "delete", "resource_id": DEPLOY_UI, "resource_type": "DEPLOYMENT",
              "provider": "kubernetes", "before": {}, "after": None}
    assert rule.evaluate(ctx(delete, twin=enriched_twin())) == []


# ------------------------------------------------------------------ bout en bout

def test_engine_scores_k8s_001():
    result = run(ctx(spec_change(DEPLOY_UI, deployment_spec(privileged=True)), twin=enriched_twin()))
    [line] = [l for l in result.risk["lines"] if l["rule_id"] == "RULE-K8S-001"]
    assert line["weight"] == 20 and line["counted"] is True


MANIFEST = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: catalog-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
  - kind: ServiceAccount
    name: catalog
    namespace: default
"""


def test_cluster_role_binding_manifest_end_to_end():
    """Manifest → ingestion → Future Twin → analyse, comme dans le dashboard."""
    out = simulate(enriched_twin(), MANIFEST, kind="kubernetes")
    findings = [f for f in out["analysis"]["findings"] if f["rule_id"] == "RULE-K8S-001"]
    assert [f["status"] for f in findings] == ["fail"]
    assert findings[0]["severity"] == "high"
    assert BINDING in findings[0]["affected_resource_ids"]


# ---------------------------------------------------------------- correctifs

def test_verified_fix_removes_privileged():
    from analysis.remediation import remediate
    report = remediate(ctx(spec_change(DEPLOY_UI, deployment_spec(privileged=True)), twin=enriched_twin()))
    data = report.model_dump(mode="json") if hasattr(report, "model_dump") else report
    # Les 20 points de K8S-001 disparaissent ; le bonus de blast radius reste (le changement a lieu).
    assert data["score_before"] - data["all_fixes"]["score_after"] == 20


def test_verified_fix_reverts_cluster_admin_binding():
    from analysis.remediation import remediate
    report = remediate(ctx(binding_create("cluster-admin", [sa("catalog")]), twin=enriched_twin()))
    data = report.model_dump(mode="json") if hasattr(report, "model_dump") else report
    assert data["score_before"] >= 20 and data["all_fixes"]["score_after"] == 0
