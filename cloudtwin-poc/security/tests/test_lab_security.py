"""Vérifications automatiques de la sécurité du labo (docs/lab-security.md).

- La politique IAM du collecteur couvre exactement les appels AWS du code, en lecture seule.
- CloudTwin s'applique ses propres règles : IAM-001 et IAM-002 ne trouvent rien.
- Le rôle Kubernetes ne lit ni les Secrets ni n'écrit quoi que ce soit.
- Aucun secret AWS n'est versionné, et Git ignore les fichiers sensibles.
"""

import ast
import json
import re
import subprocess
from pathlib import Path

import botocore.session
import pytest
import yaml
from botocore import xform_name

from analysis.rules.security._iam import parse_policies
from analysis.rules.security.iam_001_full_admin import FullAdminPolicyRule
from analysis.rules.security.iam_002_sensitive_permission import SensitivePermissionRule

POC = Path(__file__).resolve().parents[2]
REPO = POC.parent
POLICY = json.loads((POC / "security/collector-readonly-policy.json").read_text(encoding="utf-8"))
COLLECTOR = POC / "discovery/aws_collector.py"
LAB_REGION = "eu-west-3"
# Actions autorisées d'avance pour un collecteur prévu : RDS, pour RULE-RES-002.
PLANNED = {"rds:DescribeDBInstances"}
READ_PREFIXES = ("Describe", "List", "Get")


def policy_actions() -> set[str]:
    return {a for st in POLICY["Statement"] for a in st["Action"]}


def collector_actions() -> set[str]:
    """Les actions IAM réellement appelées par le collecteur, lues dans son code."""
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"))
    clients: dict[str, str] = {}      # attribut self.X -> service boto3
    calls: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "client" and node.value.args:
                target = node.targets[0]
                if isinstance(target, ast.Attribute):
                    clients[target.attr] = node.value.args[0].value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) and owner.value.id == "self":
                if node.func.attr == "get_paginator":
                    calls.add((owner.attr, node.args[0].value))
                else:
                    calls.add((owner.attr, node.func.attr))

    session = botocore.session.get_session()
    actions = set()
    for attr, method in calls:
        if attr not in clients:
            continue
        model = session.get_service_model(clients[attr])
        operations = {xform_name(op): op for op in model.operation_names}
        if method in operations:
            prefix = model.signing_name or model.endpoint_prefix
            actions.add(f"{prefix}:{operations[method]}")
    return actions


# ------------------------------------------------------------ politique IAM

def test_collector_calls_were_found():
    assert "ec2:DescribeSecurityGroups" in collector_actions()
    assert "iam:GetPolicyVersion" in collector_actions()


def test_policy_covers_every_collector_call():
    missing = collector_actions() - policy_actions()
    assert not missing, f"le collecteur appelle des actions absentes de la politique : {sorted(missing)}"


def test_policy_grants_nothing_unused():
    unused = policy_actions() - collector_actions() - PLANNED
    assert not unused, f"actions autorisées mais jamais appelées (moindre privilège) : {sorted(unused)}"


def test_policy_is_read_only_and_has_no_wildcard_action():
    for action in policy_actions():
        service, name = action.split(":")
        assert "*" not in action
        assert name.startswith(READ_PREFIXES), f"{action} n'est pas une action de lecture"


def test_regional_actions_are_locked_to_the_lab_region():
    regional = [st for st in POLICY["Statement"] if not all(a.startswith("iam:") for a in st["Action"])]
    for st in regional:
        assert st["Condition"]["StringEquals"]["aws:RequestedRegion"] == LAB_REGION


def test_cloudtwin_rules_find_nothing_dangerous_in_the_policy():
    statements, unknown = parse_policies([{"name": "collector", "document": POLICY}])
    assert unknown == []
    assert FullAdminPolicyRule().grants(statements) == {}
    assert SensitivePermissionRule().grants(statements) == {}


# -------------------------------------------------------- RBAC Kubernetes

def test_kubernetes_reader_is_read_only_and_never_reads_secrets():
    docs = list(yaml.safe_load_all((POC / "security/k8s-rbac-reader.yaml").read_text(encoding="utf-8")))
    [role] = [d for d in docs if d["kind"] == "ClusterRole"]
    for rule in role["rules"]:
        assert set(rule["verbs"]) <= {"get", "list", "watch"}
        assert "secrets" not in rule["resources"] and "*" not in rule["resources"]
    [binding] = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]


def test_collector_security_is_defined_in_terraform_but_opt_in():
    config = (REPO / "terraform/eks/minimal/collector-security.tf").read_text(encoding="utf-8")
    variables = (REPO / "terraform/eks/minimal/variables.tf").read_text(encoding="utf-8")
    assert 'resource "aws_iam_role" "cloudtwin_collector"' in config
    assert 'resource "kubernetes_cluster_role_v1" "cloudtwin_collector"' in config
    eks_module = (REPO / "terraform/lib/eks/eks.tf").read_text(encoding="utf-8")
    assert "manage_aws_auth_configmap" in eks_module
    assert 'groups   = ["cloudtwin-rbac-readers"]' in eks_module
    assert 'default     = false' in variables
    assert "secrets" not in config.lower()
    for forbidden in ('verbs      = ["*"]', 'resources  = ["*"]'):
        assert forbidden not in config


# ------------------------------------------------------------ secrets / Git

SECRET_PATTERNS = {
    "clé d'accès AWS": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "secret AWS": re.compile(r"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}"),
    "clé privée": re.compile(r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"),
}
ALLOWED_EXAMPLES = ("EXAMPLE",)   # clés factices des docs AWS (AKIAIOSFODNN7EXAMPLE)


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "cloudtwin-poc", "terraform", ".github"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("dépôt Git indisponible")
    return [REPO / p for p in out.splitlines() if p]


def test_no_aws_secret_is_committed():
    leaks = []
    for path in tracked_files():
        if path.suffix in {".png", ".jpg", ".gif", ".ico", ".zip", ".pdf"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for kind, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                if not any(ok in match.group(0) for ok in ALLOWED_EXAMPLES):
                    leaks.append(f"{path.relative_to(REPO)} : {kind}")
    assert not leaks, f"secrets versionnés : {leaks}"


@pytest.mark.parametrize("sample", ["terraform/eks/default/terraform.tfstate", "terraform/x/prod.tfvars",
                                    "cloudtwin-poc/.env", "terraform/eks/default/.terraform/plugin"])
def test_sensitive_files_are_ignored_by_git(sample):
    result = subprocess.run(["git", "check-ignore", "-q", sample], cwd=REPO)
    if result.returncode == 128:
        pytest.skip("dépôt Git indisponible")
    assert result.returncode == 0, f"{sample} n'est pas ignoré par .gitignore"
