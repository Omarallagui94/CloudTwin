"""Tests de RULE-IAM-003 (nouveau chemin Internet → rôle IAM dangereux), sur le vrai jumeau du PoC.

Dans ce jumeau, le Service ui est déjà public (NLB internet-facing), le Service
catalog est interne, et tous les Pods tournent sur un nœud du node group dont
le rôle est eks-node.
"""

import copy

from analysis.engine import run
from analysis.models import Severity, Status
from analysis.rules.security.iam_003_escalation_path import INTERNET, EscalationPathRule
from analysis.tests.test_network_rules import LB, REAL_TWIN, ctx

rule = EscalationPathRule()

NODE_ROLE = "aws:iam-role:arn:aws:iam::123456789012:role/eks-node"
APP_ARN = "arn:aws:iam::123456789012:role/catalog-app"
APP_ROLE = f"aws:iam-role:{APP_ARN}"
SA_CATALOG = "k8s:service-account:default:catalog"
SVC_CATALOG = "k8s:service:default:catalog"
PASSROLE = {"name": "ci", "document": {"Statement": [
    {"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"}]}}
ADMIN = {"name": "admin", "document": {"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}}
S3_READ = {"name": "s3-read", "document": {"Statement": [
    {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::catalog/*"}]}}


def irsa_twin(app_policies, node_policies=None):
    """Jumeau réel + un rôle IRSA catalog-app lié au ServiceAccount catalog."""
    twin = copy.deepcopy(REAL_TWIN)
    twin["resources"].append({"id": APP_ROLE, "name": "catalog-app", "type": "IAM_ROLE", "provider": "aws",
                              "properties": {"name": "catalog-app", "arn": APP_ARN,
                                             "inline_policies": app_policies}})
    for r in twin["resources"]:
        if r["id"] == SA_CATALOG:
            r["properties"]["annotations"] = {"eks.amazonaws.com/role-arn": APP_ARN}
        if r["id"] == NODE_ROLE:
            r["properties"]["inline_policies"] = node_policies or []
    return twin


def role_policies(role, before, after):
    return {"operation": "update", "resource_id": role, "resource_type": "IAM_ROLE", "provider": "aws",
            "property_path": "properties.inline_policies", "before": before, "after": after}


def catalog_exposure(after):
    return {"operation": "update", "resource_id": SVC_CATALOG, "resource_type": "SERVICE",
            "provider": "kubernetes", "property_path": "properties.service_type_and_scheme",
            "before": {"service_type": "ClusterIP"}, "after": after}


PUBLIC = {"service_type": "LoadBalancer", "scheme": "internet-facing"}


# ---------------------------------------------------- le cœur : la combinaison

def test_each_change_alone_is_not_a_path():
    twin = irsa_twin([S3_READ])
    # Service public, mais rôle inoffensif.
    [f] = rule.evaluate(ctx(catalog_exposure(PUBLIC), twin=twin))
    assert f.status == Status.PASS
    # Rôle dangereux, mais Service interne.
    [f] = rule.evaluate(ctx(role_policies(APP_ROLE, [S3_READ], [S3_READ, PASSROLE]), twin=twin))
    assert f.status == Status.PASS


def test_combination_creates_critical_irsa_path():
    twin = irsa_twin([S3_READ])
    [f] = rule.evaluate(ctx(catalog_exposure(PUBLIC),
                            role_policies(APP_ROLE, [S3_READ], [S3_READ, PASSROLE]), twin=twin))
    assert f.status == Status.FAIL and f.severity == Severity.CRITICAL
    assert f.change_ids == ["change-001", "change-002"]
    [path] = [p for p in f.evidence["new_paths"] if p["via"] == "IRSA"]
    assert path["path"][0] == INTERNET and path["path"][-1] == APP_ROLE
    assert SVC_CATALOG in path["path"] and SA_CATALOG in path["path"]
    assert path["edges"][0] == "EXPOSES (simulé)" and path["edges"][-1] == "IRSA"
    assert path["role_permissions"] == ["iam:PassRole via ci"]
    assert {SVC_CATALOG, SA_CATALOG, APP_ROLE} <= set(f.affected_resource_ids)
    assert INTERNET not in f.affected_resource_ids


def test_combination_through_the_whole_engine():
    twin = irsa_twin([S3_READ])
    result = run(ctx(catalog_exposure(PUBLIC),
                     role_policies(APP_ROLE, [S3_READ], [S3_READ, PASSROLE]), twin=twin))
    fails = {f.rule_id for f in result.findings if f.status == Status.FAIL}
    # Les règles « locales » voient chacune un morceau ; IAM-003 voit le chemin.
    assert {"RULE-NET-004", "RULE-IAM-002", "RULE-IAM-003"} <= fails


# ---------------------------------------------------- autres chemins et statuts

def test_node_role_admin_creates_imds_path_high():
    # ui est déjà public : donner *:* au rôle du nœud ouvre un chemin via l'IMDS.
    [f] = rule.evaluate(ctx(role_policies(NODE_ROLE, [], [ADMIN])))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
    [path] = f.evidence["new_paths"]
    assert path["via"] == "IMDS" and path["path"][:2] == [INTERNET, LB]
    assert path["edges"][-1] == "USES"   # node group -> rôle


def test_irsa_annotation_change_creates_path():
    twin = irsa_twin([ADMIN])
    # Le ServiceAccount ui (Service public) reçoit le rôle admin catalog-app.
    change = {"operation": "update", "resource_id": "k8s:service-account:default:ui",
              "resource_type": "SERVICE_ACCOUNT", "provider": "kubernetes",
              "property_path": "properties.annotations", "before": {},
              "after": {"eks.amazonaws.com/role-arn": APP_ARN}}
    [f] = rule.evaluate(ctx(change, twin=twin))
    assert f.status == Status.FAIL and f.severity == Severity.CRITICAL


def test_closing_the_service_removes_the_path():
    twin = irsa_twin([PASSROLE])
    for r in twin["resources"]:                       # catalog est public avant le changement
        if r["id"] == SVC_CATALOG:
            r["properties"]["service_type"] = "LoadBalancer"
    twin["relationships"].append({"source": LB, "target": SVC_CATALOG, "type": "ROUTES_TO"})
    change = catalog_exposure({"service_type": "ClusterIP"})
    change["before"] = PUBLIC
    [f] = rule.evaluate(ctx(change, twin=twin))
    assert f.status == Status.PASS


def test_existing_path_is_medium():
    twin = irsa_twin([], node_policies=[ADMIN])
    [f] = rule.evaluate(ctx(role_policies(APP_ROLE, [], [S3_READ]), twin=twin))
    assert f.status == Status.FAIL and f.severity == Severity.MEDIUM
    assert f.evidence["new_paths"] == [] and f.evidence["existing_paths"]


def test_unknown_policy_on_reachable_role_is_not_evaluated():
    custom = "arn:aws:iam::123456789012:policy/custom"
    change = {**role_policies(NODE_ROLE, [], [custom]), "property_path": "properties.managed_policy_arns"}
    [f] = rule.evaluate(ctx(change))
    assert f.status == Status.NOT_EVALUATED


def test_unknown_service_exposure_is_not_evaluated():
    twin = irsa_twin([PASSROLE])
    [f] = rule.evaluate(ctx(catalog_exposure({"service_type": "NodePort"}), twin=twin))
    assert f.status == Status.NOT_EVALUATED


def test_unrelated_change_produces_nothing():
    change = {"operation": "update", "resource_id": "k8s:deployment:default:ui", "resource_type": "DEPLOYMENT",
              "provider": "kubernetes", "property_path": "properties.replicas", "before": 3, "after": 1}
    assert rule.evaluate(ctx(change)) == []


def test_role_without_collected_policies_is_unknown_not_safe():
    # Dans le jumeau réel brut, eks-node n'a pas de politiques collectées : jamais un faux « pass ».
    [f] = rule.evaluate(ctx(catalog_exposure(PUBLIC)))
    assert f.status == Status.NOT_EVALUATED
    assert f"politiques de {NODE_ROLE}" in f.evidence["unknown"]
