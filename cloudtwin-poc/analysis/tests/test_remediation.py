"""Tests des correctifs vérifiés par le jumeau (analysis/remediation)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from analysis.engine import run
from analysis.remediation import REVERT, Fix, apply, propose, remediate, summary
from analysis.tests.test_network_rules import LB, SG, VPC_ONLY, ctx, sg_change, tf_rule
from analysis.tests.test_scoring import demo_changes

POC = Path(__file__).resolve().parents[2]
NODE_ROLE = "aws:iam-role:arn:aws:iam::123456789012:role/eks-node"


def by_rule(report, rule_id):
    return next(o for o in report.options if rule_id in o.rule_ids)


# --------------------------------------------------------- scénario de démo

def test_demo_each_fix_is_verified_and_all_together_reach_zero():
    report = remediate(demo_changes("security_group"))
    assert report.score_before == 80
    assert [o.score_after for o in report.options] == [60, 60, 60, 60]
    assert all(o.verified and not o.new_risks for o in report.options)
    assert report.all_fixes.score_after == 0 and report.all_fixes.level_after == "Faible"


def test_cutting_the_attack_path_is_ranked_first():
    report = remediate(demo_changes("service"))
    best = report.options[0]
    assert best.rule_ids == ["RULE-NET-004"] and best.score_after == 60
    assert best.resolved == ["RULE-IAM-003", "RULE-NET-004"]        # un correctif, deux risques
    assert by_rule(report, "RULE-RES-001").score_after == 90


def test_summary_reads_before_and_after():
    text = summary(remediate(demo_changes("security_group")))
    assert text.startswith("Score actuel : 80/100") and "→ 60/100 (-20)" in text and "→ 0/100" in text


# -------------------------------------------------------------- correctifs

def test_security_group_fix_restricts_to_the_vpc_cidr():
    c = ctx(sg_change(VPC_ONLY, VPC_ONLY + [tf_rule(22, ["0.0.0.0/0"]),
                                            {"protocol": "tcp", "from_port": 80, "to_port": 80,
                                             "ipv6_cidr_blocks": ["::/0"]}]))
    [fix] = propose(c, run(c).findings)          # NET-001 et NET-002 : une seule option réseau
    assert {"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr_blocks": ["10.0.0.0/16"]} in fix.after
    assert all("::/0" not in r.get("ipv6_cidr_blocks", []) for r in fix.after)
    outcome = remediate(c).options[0]
    assert outcome.verified and outcome.score_after < outcome.score_before


def test_security_group_fix_reads_the_aws_api_format():
    after = [{"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    c = ctx(sg_change([], after, path="properties.ingress_rules"))
    [fix] = propose(c, run(c).findings)
    assert fix.after == [{"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432,
                          "IpRanges": [{"CidrIp": "10.0.0.0/16"}]}]


def test_iam_fix_keeps_harmless_statements():
    ecr = {"Effect": "Allow", "Action": "ecr:BatchGetImage", "Resource": "*"}
    passrole = {"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"}
    tf_policy = {"name": "ci", "policy": json.dumps({"Statement": [ecr, passrole]})}
    change = {"operation": "update", "resource_id": NODE_ROLE, "resource_type": "IAM_ROLE", "provider": "aws",
              "property_path": "properties.inline_policy", "before": [], "after": [tf_policy]}
    c = ctx(change)
    [fix] = propose(c, run(c).findings)
    [kept] = fix.after
    assert json.loads(kept["policy"])["Statement"] == [ecr]


def test_iam_fix_removes_administrator_access():
    admin = "arn:aws:iam::aws:policy/AdministratorAccess"
    readonly = "arn:aws:iam::aws:policy/ReadOnlyAccess"
    change = {"operation": "update", "resource_id": NODE_ROLE, "resource_type": "IAM_ROLE", "provider": "aws",
              "property_path": "properties.managed_policy_arns", "before": [readonly], "after": [readonly, admin]}
    c = ctx(change)
    fixes = propose(c, run(c).findings)
    assert fixes and fixes[0].after == [readonly]


def test_deletion_fix_reverts_the_change():
    change = {"operation": "delete", "resource_id": "k8s:service:default:ui", "resource_type": "SERVICE",
              "provider": "kubernetes", "property_path": None}
    c = ctx(change)
    [fix] = [f for f in propose(c, run(c).findings) if f.rule_id == "RULE-RES-003"]
    assert fix.reverts and apply(c, [fix]).changes == []


# ------------------------------------------------------------ vérification

def test_a_fix_that_changes_nothing_is_not_verified():
    c = demo_changes("security_group")
    useless = Fix("change-001", "RULE-RES-001", "Garder 1 réplica", 1, origin="llm")
    outcome = remediate(c, extra=[useless]).options[-1]
    assert outcome.origin == "llm" and outcome.score_after == 80 and not outcome.verified


def test_a_fix_that_creates_a_new_risk_is_rejected():
    c = demo_changes("security_group")
    bad = Fix("change-003", "RULE-NET-002", "Fermer 8080 mais ouvrir SSH", [tf_rule(22, ["0.0.0.0/0"])], "llm")
    outcome = next(o for o in remediate(c, extra=[bad]).options if o.origin == "llm")
    assert outcome.new_risks == ["RULE-NET-001"] and not outcome.verified


def test_original_context_is_never_modified():
    c = demo_changes("security_group")
    before = c.model_dump_json()
    remediate(c)
    assert c.model_dump_json() == before


def test_cli_fixes(tmp_path):
    path = tmp_path / "ctx.json"
    path.write_text(demo_changes("service").model_dump_json(), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CLOUDTWIN_LLM"}
    out = subprocess.run([sys.executable, "-m", "analysis", str(path), "--fixes"], cwd=POC,
                         capture_output=True, text=True, env=env)
    report = json.loads(out.stdout)["remediation"]
    assert report["score_before"] == 100 and report["all_fixes"]["score_after"] == 0
    assert "Score actuel : 100/100" in out.stderr
