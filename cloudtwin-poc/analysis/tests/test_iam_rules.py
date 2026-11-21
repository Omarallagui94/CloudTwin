"""Tests de RULE-IAM-001 (*:*) et RULE-IAM-002 (permission sensible), sur le vrai jumeau du PoC."""

import json

import pytest

from analysis.engine import run
from analysis.models import Severity, Status
from analysis.rules.security.iam_001_full_admin import FullAdminPolicyRule
from analysis.rules.security.iam_002_sensitive_permission import SensitivePermissionRule
from analysis.tests.test_network_rules import REAL_TWIN, ctx
from ingestion import ChangeIngester
from integration.hamdi_fatma import context_from_changeset

NODE_ROLE_ARN = "arn:aws:iam::123456789012:role/eks-node"
NODE_ROLE = f"aws:iam-role:{NODE_ROLE_ARN}"
ADMIN_ARN = "arn:aws:iam::aws:policy/AdministratorAccess"
ECR_READ = {"name": "ecr-read", "document": {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken", "ecr:BatchGetImage"], "Resource": "*"}]}}


def inline(name, *statements):
    return {"name": name, "document": {"Version": "2012-10-17", "Statement": list(statements)}}


def allow(action, resource="*", **extra):
    return {"Effect": "Allow", "Action": action, "Resource": resource, **extra}


def role_change(before, after, path="properties.inline_policies", operation="update"):
    return {"operation": operation, "resource_id": NODE_ROLE, "resource_type": "IAM_ROLE",
            "provider": "aws", "property_path": path, "before": before, "after": after}


# ----------------------------------------------------------------- RULE-IAM-001

class TestIam001:
    rule = FullAdminPolicyRule()

    def test_inline_star_star_fails_critical(self):
        [f] = self.rule.evaluate(ctx(role_change([ECR_READ], [ECR_READ, inline("oops", allow("*"))])))
        assert f.status == Status.FAIL and f.severity == Severity.CRITICAL
        # Sur le vrai jumeau, le node group EKS utilise ce rôle.
        assert f.affected_resource_ids == sorted([NODE_ROLE, "aws:eks-nodegroup:retail-store:default"])
        assert "1.16" in f.reference and f.remediation

    def test_administrator_access_arn_fails(self):
        [f] = self.rule.evaluate(ctx(role_change([], [ADMIN_ARN], path="properties.managed_policy_arns")))
        assert f.status == Status.FAIL
        assert f.evidence["newly_granted"][0]["policy"] == ADMIN_ARN

    def test_discovery_format_attached_policy(self):
        attached = {"name": "AdministratorAccess", "arn": ADMIN_ARN}   # document absent : connu par l'ARN
        [f] = self.rule.evaluate(ctx(role_change([], [attached], path="properties.attached_policies")))
        assert f.status == Status.FAIL

    def test_terraform_inline_policy_json_string(self):
        tf = {"name": "admin", "policy": json.dumps({"Statement": [allow(["*"])]})}
        [f] = self.rule.evaluate(ctx(role_change([], [tf], path="properties.inline_policy")))
        assert f.status == Status.FAIL

    def test_star_action_on_one_bucket_is_not_full_admin(self):
        [f] = self.rule.evaluate(ctx(role_change([], [inline("s3", allow("*", "arn:aws:s3:::logs/*"))])))
        assert f.status == Status.PASS

    def test_existing_admin_is_still_reported(self):
        admin = inline("admin", allow("*"))
        [f] = self.rule.evaluate(ctx(role_change([admin], [admin, ECR_READ])))
        assert f.status == Status.FAIL and f.severity == Severity.MEDIUM

    def test_unknown_customer_policy_is_not_evaluated(self):
        arn = "arn:aws:iam::123456789012:policy/custom"
        [f] = self.rule.evaluate(ctx(role_change([], [arn], path="properties.managed_policy_arns")))
        assert f.status == Status.NOT_EVALUATED and f.evidence["unknown_policies"] == [arn]

    def test_unreadable_after_is_not_evaluated(self):
        [f] = self.rule.evaluate(ctx(role_change([], None)))
        assert f.status == Status.NOT_EVALUATED

    def test_deny_statement_is_ignored(self):
        [f] = self.rule.evaluate(ctx(role_change([], [inline("d", {"Effect": "Deny", "Action": "*",
                                                                    "Resource": "*"})])))
        assert f.status == Status.PASS


# ----------------------------------------------------------------- RULE-IAM-002

class TestIam002:
    rule = SensitivePermissionRule()

    def test_passrole_on_star_fails_high(self):
        [f] = self.rule.evaluate(ctx(role_change([ECR_READ], [ECR_READ, inline("ci", allow("iam:PassRole"))])))
        assert f.status == Status.FAIL and f.severity == Severity.HIGH
        assert [d["action"] for d in f.evidence["newly_granted"]] == ["iam:PassRole"]
        assert "T1098.003" in f.reference

    def test_passrole_on_one_role_is_medium(self):
        target = "arn:aws:iam::123456789012:role/app-runtime"
        [f] = self.rule.evaluate(ctx(role_change([], [inline("ci", allow("iam:PassRole", target))])))
        assert f.status == Status.FAIL and f.severity == Severity.MEDIUM

    def test_iam_wildcard_grants_all_sensitive_actions(self):
        [f] = self.rule.evaluate(ctx(role_change([], [inline("iam", allow("iam:*"))])))
        assert f.status == Status.FAIL
        assert {d["action"] for d in f.evidence["newly_granted"]} >= {"iam:PassRole", "iam:PutRolePolicy"}

    def test_case_insensitive_and_partial_wildcard(self):
        [f] = self.rule.evaluate(ctx(role_change([], [inline("x", allow("IAM:pass*"))])))
        assert [d["action"] for d in f.evidence["newly_granted"]] == ["iam:PassRole"]

    def test_not_action_allow_is_detected(self):
        st = {"Effect": "Allow", "NotAction": ["s3:*"], "Resource": "*"}
        [f] = self.rule.evaluate(ctx(role_change([], [inline("broad", st)])))
        assert f.status == Status.FAIL

    def test_full_admin_is_left_to_iam_001(self):
        [f] = self.rule.evaluate(ctx(role_change([], [inline("admin", allow("*"))])))
        assert f.status == Status.PASS

    def test_iam_full_access_managed_policy(self):
        arn = "arn:aws:iam::aws:policy/IAMFullAccess"
        [f] = self.rule.evaluate(ctx(role_change([], [arn], path="properties.managed_policy_arns")))
        assert f.status == Status.FAIL

    def test_harmless_permissions_pass(self):
        [f] = self.rule.evaluate(ctx(role_change([], [ECR_READ, inline("r", allow("iam:GetRole"))])))
        assert f.status == Status.PASS and f.remediation is None

    def test_other_property_is_ignored(self):
        assert self.rule.evaluate(ctx(role_change(3600, 7200, path="properties.max_session_duration"))) == []


# ------------------------------------------- bout en bout : plan Terraform réel

def _plan(before_inline, after_inline):
    state = {"name": "eks-node", "arn": NODE_ROLE_ARN, "id": "eks-node"}
    return {"format_version": "1.2", "resource_changes": [{
        "address": "aws_iam_role.eks_node", "mode": "managed", "type": "aws_iam_role",
        "name": "eks_node", "change": {"actions": ["update"],
                                       "before": {**state, "inline_policy": before_inline},
                                       "after": {**state, "inline_policy": after_inline},
                                       "after_unknown": {}}}]}


@pytest.mark.parametrize("statement, rule_id, severity", [
    (allow("*"), "RULE-IAM-001", Severity.CRITICAL),
    (allow("iam:PassRole"), "RULE-IAM-002", Severity.HIGH),
])
def test_terraform_plan_through_ingestion(statement, rule_id, severity):
    after = [{"name": "extra", "policy": json.dumps({"Version": "2012-10-17", "Statement": [statement]})}]
    changeset = ChangeIngester(REAL_TWIN).ingest_terraform_plan(_plan([], after))
    result = run(context_from_changeset(changeset, REAL_TWIN))
    fails = [f for f in result.findings if f.status == Status.FAIL]
    assert [(f.rule_id, f.severity) for f in fails] == [(rule_id, severity)]
    assert fails[0].change_ids == [changeset.changes[0].change_id]
