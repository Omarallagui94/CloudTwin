"""Tests du score global (analysis/scoring), dont le scénario de démonstration §11."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.engine import run
from analysis.models import (SCHEMA_VERSION, AffectedResource, AnalysisContext, Category, Change, Finding,
                             Severity, Status, Twin)
from analysis.scoring.score import compute_score, explain, level_for
from analysis.tests.test_network_rules import scenario_context

POC_ROOT = Path(__file__).resolve().parents[2]


def finding(rule_id, severity, change_ids=("c1",), status=Status.FAIL):
    return Finding(rule_id=rule_id, rule_version="1.0", status=status, category=Category.SECURITY,
                   severity=severity, title="t", description="d", change_ids=list(change_ids))


def indirect(target, change_id="c1"):
    return AffectedResource(change_id=change_id, source="x", target=target, relationship="USES",
                            direction="outgoing", distance=2, impact="indirect", path=["x", "y", target])


# ------------------------------------------------------------------ formule

@pytest.mark.parametrize("score, label", [
    (0, "Faible"), (29, "Faible"), (30, "Moyen"), (59, "Moyen"),
    (60, "Élevé"), (79, "Élevé"), (80, "Critique"), (100, "Critique"),
])
def test_levels(score, label):
    assert level_for(score)[1] == label


def test_weights_add_up():
    s = compute_score([finding("RULE-RES-001", Severity.HIGH, ["c1"]),
                       finding("RULE-IAM-001", Severity.CRITICAL, ["c2"]),
                       finding("RULE-RES-003", Severity.MEDIUM, ["c3"]),
                       finding("RULE-X", Severity.LOW, ["c4"])])
    assert s.score == 20 + 30 + 10 + 5 and s.label == "Élevé"


def test_only_fail_counts():
    s = compute_score([finding("RULE-RES-001", Severity.HIGH, status=Status.PASS)])
    assert s.score == 0 and s.lines == []


def test_same_rule_on_two_changes_counts_twice():
    s = compute_score([finding("RULE-RES-001", Severity.HIGH, ["c1"]),
                       finding("RULE-RES-001", Severity.HIGH, ["c2"])])
    assert s.score == 40


def test_overlapping_network_rules_count_once():
    s = compute_score([finding("RULE-NET-002", Severity.HIGH, ["c1"]),
                       finding("RULE-NET-003", Severity.CRITICAL, ["c1"])])
    assert s.score == 30
    net002 = next(l for l in s.lines if l.rule_id == "RULE-NET-002")
    assert not net002.counted and "RULE-NET-003" in net002.reason


def test_capped_at_100():
    s = compute_score([finding("RULE-IAM-001", Severity.CRITICAL, [f"c{i}"]) for i in range(5)])
    assert s.score == 100 and s.findings_points == 150
    assert "plafonné" in explain(s)


def test_blast_radius_bonus_is_unique_and_capped():
    s = compute_score([finding("RULE-RES-001", Severity.HIGH)],
                      [indirect("a"), indirect("a"), indirect("b")])
    assert s.blast_radius_bonus == 2 and s.score == 22
    s = compute_score([], [indirect(f"r{i}") for i in range(15)])
    assert s.blast_radius_bonus == 10


def test_not_evaluated_makes_score_a_minimum():
    s = compute_score([finding("RULE-RES-001", Severity.HIGH),
                       finding("RULE-IAM-003", Severity.CRITICAL, status=Status.NOT_EVALUATED)])
    assert s.partial and s.not_evaluated == 1 and s.score == 20
    assert "minimum" in explain(s)


def test_explanation_lets_the_jury_recompute():
    s = compute_score([finding("RULE-RES-001", Severity.HIGH, ["c1"]),
                       finding("RULE-IAM-003", Severity.CRITICAL, ["c2"])])
    assert explain(s) == "RULE-IAM-003 CRITICAL +30 + RULE-RES-001 HIGH +20 = 50/100 → Moyen"


def test_s04_end_to_end_counts_the_open_database_once():
    result = run(scenario_context("S04", "proposed-plan.json"))
    assert result.risk["score"] == 30                      # NET-003 CRITICAL ; NET-002 non recompté
    assert result.risk["label"] == "Moyen"


# --------------------------------------------- scénario de démonstration §11

API_ROLE_ARN = "arn:aws:iam::123456789012:role/api"
DEMO_TWIN = {
    "resources": [
        {"id": "k8s:deployment:prod:api", "name": "api", "type": "DEPLOYMENT", "provider": "kubernetes",
         "properties": {"namespace": "prod", "replicas": 3}},
        {"id": "k8s:service:prod:api", "name": "api", "type": "SERVICE", "provider": "kubernetes",
         "properties": {"namespace": "prod", "service_type": "ClusterIP", "annotations": {}}},
        {"id": "k8s:service-account:prod:api", "name": "api", "type": "SERVICE_ACCOUNT",
         "provider": "kubernetes",
         "properties": {"namespace": "prod", "annotations": {"eks.amazonaws.com/role-arn": API_ROLE_ARN}}},
        {"id": f"aws:iam-role:{API_ROLE_ARN}", "name": "api", "type": "IAM_ROLE", "provider": "aws",
         "properties": {"arn": API_ROLE_ARN, "attached_policies": [], "inline_policies": []}},
        {"id": "aws:rds:main", "name": "main", "type": "RDS_INSTANCE", "provider": "aws",
         "properties": {"multi_az": True}},
        {"id": "aws:security-group:sg-api", "name": "api", "type": "SECURITY_GROUP", "provider": "aws",
         "properties": {"group_id": "sg-api"}},
    ],
    "relationships": [
        {"source": "k8s:service:prod:api", "target": "k8s:deployment:prod:api", "type": "EXPOSES"},
        {"source": "k8s:deployment:prod:api", "target": "k8s:service-account:prod:api", "type": "USES"},
        {"source": "k8s:deployment:prod:api", "target": "aws:rds:main", "type": "USES"},
        {"source": "k8s:deployment:prod:api", "target": "aws:security-group:sg-api", "type": "USES"},
    ],
}
PASSROLE = [{"name": "deploy", "document": {"Statement": [
    {"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"}]}}]
VPC_8080 = {"protocol": "tcp", "from_port": 8080, "to_port": 8080, "cidr_blocks": ["10.0.0.0/16"]}


def demo_changes(expose_with):
    exposure = {
        "security_group": dict(resource_id="aws:security-group:sg-api", resource_type="SECURITY_GROUP",
                               provider="aws", property_path="properties.ingress", before=[VPC_8080],
                               after=[{**VPC_8080, "cidr_blocks": ["0.0.0.0/0"]}]),
        "service": dict(resource_id="k8s:service:prod:api", resource_type="SERVICE", provider="kubernetes",
                        property_path="properties.service_type_and_scheme",
                        before={"service_type": "ClusterIP"},
                        after={"service_type": "LoadBalancer", "scheme": "internet-facing"}),
    }[expose_with]
    raw = [
        dict(resource_id="k8s:deployment:prod:api", resource_type="DEPLOYMENT", provider="kubernetes",
             property_path="properties.replicas", before=3, after=1),
        dict(resource_id="aws:rds:main", resource_type="RDS_INSTANCE", provider="aws",
             property_path="properties.multi_az", before=True, after=False),
        exposure,
        dict(resource_id=f"aws:iam-role:{API_ROLE_ARN}", resource_type="IAM_ROLE", provider="aws",
             property_path="properties.inline_policies", before=[], after=PASSROLE),
    ]
    return AnalysisContext(
        schema_version=SCHEMA_VERSION, simulation_id=f"sim-demo-{expose_with}",
        current_twin=Twin.model_validate(DEMO_TWIN),
        changes=[Change(change_id=f"change-{i:03d}", operation="update", **c) for i, c in enumerate(raw, 1)])


def test_demo_as_in_the_presentation_security_group():
    # Réplicas 3→1, Multi-AZ off, SG 10.0.0.0/16 → 0.0.0.0/0, ajout de iam:PassRole.
    result = run(demo_changes("security_group"))
    counted = sorted(l["rule_id"] for l in result.risk["lines"] if l["counted"])
    assert counted == ["RULE-IAM-002", "RULE-NET-002", "RULE-RES-001", "RULE-RES-002"]
    assert result.risk["score"] == 80 and result.risk["label"] == "Critique"
    assert not result.risk["partial"]


def test_demo_with_public_service_reveals_the_combination():
    # Même scénario, mais l'exposition passe par le Service : IAM-003 voit le chemin complet.
    result = run(demo_changes("service"))
    counted = sorted(l["rule_id"] for l in result.risk["lines"] if l["counted"])
    assert counted == ["RULE-IAM-002", "RULE-IAM-003", "RULE-NET-004", "RULE-RES-001", "RULE-RES-002"]
    assert result.risk["findings_points"] == 110 and result.risk["score"] == 100


# ------------------------------------------------------------- ligne de commande

def test_cli_fail_at_blocks_a_risky_change(tmp_path):
    path = tmp_path / "ctx.json"
    path.write_text(demo_changes("security_group").model_dump_json(), encoding="utf-8")
    cmd = [sys.executable, "-m", "analysis", str(path), "--fail-at"]
    blocked = subprocess.run(cmd + ["80"], cwd=POC_ROOT, capture_output=True,
                             text=True, encoding="utf-8")
    allowed = subprocess.run(cmd + ["81"], cwd=POC_ROOT, capture_output=True,
                             text=True, encoding="utf-8")
    assert blocked.returncode == 1 and allowed.returncode == 0
    assert json.loads(blocked.stdout)["risk"]["score"] == 80
    assert "= 80/100 → Critique" in blocked.stderr
from analysis.__main__ import risk_exit_code


@pytest.mark.parametrize(("score", "expected"), [(79, 0), (80, 1), (81, 1), (100, 1)])
def test_fail_at_80_boundary(score, expected):
    assert risk_exit_code(score, 80) == expected
