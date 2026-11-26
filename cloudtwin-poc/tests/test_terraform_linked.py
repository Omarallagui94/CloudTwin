"""H4 : politiques IAM, RDS et règles de Security Group déclarées à part dans un plan Terraform.

Chaque test passe par toute la chaîne (plan → ingestion → Future Twin → règles), comme
`python -m simulation --change tfplan.json`.
"""

import json

from ingestion import ChangeIngester
from simulation.pipeline import simulate

ADMIN = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}
PASSROLE = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"}]}
READ_S3 = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:GetObject",
                                                   "Resource": "arn:aws:s3:::orders/*"}]}

TWIN = {
    "schema_version": "1.0.0",
    "metadata": {"provider": "AWS", "region": "eu-west-3"},
    "resources": [
        {"id": "aws:iam-role:app-role", "name": "app-role", "type": "IAM_ROLE", "provider": "aws",
         "properties": {"arn": "arn:aws:iam::123456789012:role/app-role",
                        "attached_policies": [{"name": "orders-read", "arn": "arn:aws:iam::123456789012:policy/orders-read",
                                               "document": READ_S3}]}},
        {"id": "aws:security-group:sg-0db", "name": "orders-db", "type": "SECURITY_GROUP", "provider": "aws",
         "properties": {"group_id": "sg-0db", "ingress": [
             {"protocol": "tcp", "from_port": 5432, "to_port": 5432, "cidr_blocks": ["10.0.0.0/16"],
              "ipv6_cidr_blocks": []}]}},
        {"id": "aws:rds-instance:orders-db", "name": "orders-db", "type": "RDS_INSTANCE", "provider": "aws",
         "properties": {"db_instance_identifier": "orders-db", "engine": "postgres", "multi_az": True}},
    ],
    "relationships": [],
}


def rc(address, actions, before=None, after=None, after_unknown=None):
    rtype, name = address.split(".")
    return {"address": address, "mode": "managed", "type": rtype, "name": name,
            "change": {"actions": actions, "before": before, "after": after,
                       "after_unknown": after_unknown or {}}}


def plan(*changes, config=None):
    return {"format_version": "1.2", "terraform_version": "1.9.0", "resource_changes": list(changes),
            "configuration": {"root_module": {"resources": config or []}}}


def run(p):
    return simulate(json.loads(json.dumps(TWIN)), p, kind="terraform")


def status(out, rule):
    return [(f["status"], f["severity"]) for f in out["analysis"]["findings"] if f["rule_id"] == rule]


def coverage(p):
    return ChangeIngester(TWIN).ingest(p, kind="terraform_plan").metadata["terraform_coverage"]


# ------------------------------------------------------------------ IAM

def test_inline_policy_with_passrole_is_an_escalation_permission():
    out = run(plan(rc("aws_iam_role_policy.extra", ["create"],
                      after={"role": "app-role", "name": "extra", "policy": json.dumps(PASSROLE)})))
    assert ("fail", "high") in status(out, "RULE-IAM-002")
    [change] = out["changes"]["changes"]
    assert change["resource_id"] == "aws:iam-role:app-role"
    assert change["property_path"] == "properties.inline_policy"


def test_attaching_administrator_access_is_critical():
    out = run(plan(rc("aws_iam_role_policy_attachment.admin", ["create"],
                      after={"role": "app-role", "policy_arn": "arn:aws:iam::aws:policy/AdministratorAccess"})))
    assert ("fail", "critical") in status(out, "RULE-IAM-001")
    [change] = out["changes"]["changes"]
    # La politique déjà attachée reste dans la liste : seule la nouvelle est ajoutée.
    assert [p["name"] for p in change["after"]] == ["orders-read", "AdministratorAccess"]


def test_attachment_of_a_policy_created_in_the_same_plan_reads_its_document():
    out = run(plan(
        rc("aws_iam_policy.wide", ["create"], after={"name": "wide", "policy": json.dumps(ADMIN)},
           after_unknown={"arn": True}),
        rc("aws_iam_role_policy_attachment.wide", ["create"], after={"role": "app-role"},
           after_unknown={"policy_arn": True}),
        config=[{"address": "aws_iam_role_policy_attachment.wide",
                 "expressions": {"policy_arn": {"references": ["aws_iam_policy.wide.arn", "aws_iam_policy.wide"]},
                                 "role": {"constant_value": "app-role"}}}],
    ))
    assert ("fail", "critical") in status(out, "RULE-IAM-001")


def test_role_created_in_the_same_plan_is_found_through_references():
    p = plan(
        rc("aws_iam_role.worker", ["create"], after={"name": "worker", "assume_role_policy": "{}"}),
        rc("aws_iam_role_policy.worker", ["create"], after={"name": "worker", "policy": json.dumps(PASSROLE)},
           after_unknown={"role": True}),
        config=[{"address": "aws_iam_role_policy.worker",
                 "expressions": {"role": {"references": ["aws_iam_role.worker.id", "aws_iam_role.worker"]}}}],
    )
    targets = [c["resource_id"] for c in ChangeIngester(TWIN).ingest(p, kind="terraform_plan").to_dict()["changes"]]
    assert targets == ["aws:iam-role:worker", "aws:iam-role:worker"]
    assert coverage(p)["status"] == "complete"


def test_two_inline_policies_on_the_same_role_accumulate():
    changes = ChangeIngester(TWIN).ingest(plan(
        rc("aws_iam_role_policy.a", ["create"], after={"role": "app-role", "name": "a", "policy": json.dumps(READ_S3)}),
        rc("aws_iam_role_policy.b", ["create"], after={"role": "app-role", "name": "b", "policy": json.dumps(PASSROLE)}),
    ), kind="terraform_plan").to_dict()["changes"]
    assert [p["name"] for p in changes[1]["before"]] == ["a"]
    assert [p["name"] for p in changes[1]["after"]] == ["a", "b"]


def test_deleting_an_inline_policy_removes_it():
    p = plan(rc("aws_iam_role_policy.extra", ["delete"],
                before={"role": "app-role", "name": "extra", "policy": json.dumps(PASSROLE)}))
    [change] = ChangeIngester(TWIN).ingest(p, kind="terraform_plan").to_dict()["changes"]
    assert change["after"] == []
    assert status(run(p), "RULE-IAM-002") == [("pass", "high")]


def test_editing_an_attached_customer_policy_updates_every_role_using_it():
    p = plan(rc("aws_iam_policy.orders_read", ["update"],
                before={"arn": "arn:aws:iam::123456789012:policy/orders-read", "policy": json.dumps(READ_S3)},
                after={"arn": "arn:aws:iam::123456789012:policy/orders-read", "policy": json.dumps(ADMIN)}))
    out = run(p)
    assert ("fail", "critical") in status(out, "RULE-IAM-001")


def test_a_standalone_new_policy_is_ignored_until_attached():
    cov = coverage(plan(rc("aws_iam_policy.unused", ["create"], after={"name": "unused", "policy": "{}"})))
    assert cov["status"] == "complete"
    assert cov["ignored_non_semantic"][0]["type"] == "aws_iam_policy"


# ------------------------------------------------------------------ RDS

def test_rds_multi_az_disabled_is_detected_from_a_real_plan():
    out = run(plan(rc("aws_db_instance.orders", ["update"],
                      before={"id": "db-ABCDEF", "identifier": "orders-db", "multi_az": True},
                      after={"id": "db-ABCDEF", "identifier": "orders-db", "multi_az": False})))
    assert ("fail", "high") in status(out, "RULE-RES-002")
    [change] = out["changes"]["changes"]
    assert change["resource_id"] == "aws:rds-instance:orders-db"


def test_new_rds_without_multi_az_is_named_by_its_identifier():
    p = plan(rc("aws_db_instance.reports", ["create"], after={"identifier": "reports-db", "multi_az": False}))
    [change] = ChangeIngester(TWIN).ingest(p, kind="terraform_plan").to_dict()["changes"]
    assert change["resource_id"] == "aws:rds-instance:reports-db"


# ----------------------------------------------------- Security Groups

def test_separate_sg_rule_opening_postgres_to_internet_is_critical():
    out = run(plan(rc("aws_security_group_rule.pg_public", ["create"],
                      after={"type": "ingress", "security_group_id": "sg-0db", "protocol": "tcp",
                             "from_port": 5432, "to_port": 5432, "cidr_blocks": ["0.0.0.0/0"]})))
    assert ("fail", "critical") in status(out, "RULE-NET-003")
    [change] = out["changes"]["changes"]
    assert change["property_path"] == "properties.ingress" and len(change["after"]) == 2


def test_vpc_ingress_rule_opening_ssh_is_detected():
    out = run(plan(rc("aws_vpc_security_group_ingress_rule.ssh", ["create"],
                      after={"security_group_id": "sg-0db", "ip_protocol": "tcp", "from_port": 22,
                             "to_port": 22, "cidr_ipv4": "0.0.0.0/0"})))
    assert ("fail", "high") in status(out, "RULE-NET-001")


def test_vpc_ingress_rule_all_traffic_is_mapped_to_all_ports():
    p = plan(rc("aws_vpc_security_group_ingress_rule.all", ["create"],
                after={"security_group_id": "sg-0db", "ip_protocol": "-1", "cidr_ipv4": "0.0.0.0/0"}))
    [change] = ChangeIngester(TWIN).ingest(p, kind="terraform_plan").to_dict()["changes"]
    assert change["after"][-1] == {"protocol": "-1", "from_port": 0, "to_port": 0,
                                   "cidr_blocks": ["0.0.0.0/0"], "ipv6_cidr_blocks": []}
    assert ("fail", "critical") in status(run(p), "RULE-NET-003")


def test_egress_rules_are_ignored_and_reported():
    cov = coverage(plan(
        rc("aws_security_group_rule.out", ["create"],
           after={"type": "egress", "security_group_id": "sg-0db", "protocol": "-1",
                  "from_port": 0, "to_port": 0, "cidr_blocks": ["0.0.0.0/0"]}),
        rc("aws_vpc_security_group_egress_rule.out", ["create"],
           after={"security_group_id": "sg-0db", "ip_protocol": "-1", "cidr_ipv4": "0.0.0.0/0"}),
    ))
    assert cov["status"] == "complete" and len(cov["ignored_non_semantic"]) == 2


def test_rule_on_an_unknown_security_group_is_reported_not_guessed():
    p = plan(rc("aws_security_group_rule.x", ["create"],
                after={"type": "ingress", "security_group_id": "sg-absent", "protocol": "tcp",
                       "from_port": 443, "to_port": 443, "cidr_blocks": ["0.0.0.0/0"]}))
    cov = coverage(p)
    assert cov["status"] == "partial"
    assert "sg-absent" in cov["unsupported_material"][0]["reason"]
    assert ChangeIngester(TWIN).ingest(p, kind="terraform_plan").to_dict()["changes"] == []


def test_security_group_created_in_the_same_plan_is_found_through_references():
    p = plan(
        rc("aws_security_group.web", ["create"], after={"name": "web", "tags": {"Name": "web"}},
           after_unknown={"id": True}),
        rc("aws_security_group_rule.web_https", ["create"],
           after={"type": "ingress", "protocol": "tcp", "from_port": 443, "to_port": 443,
                  "cidr_blocks": ["0.0.0.0/0"]}, after_unknown={"security_group_id": True}),
        config=[{"address": "aws_security_group_rule.web_https",
                 "expressions": {"security_group_id": {"references": ["aws_security_group.web.id",
                                                                       "aws_security_group.web"]}}}],
    )
    changes = ChangeIngester(TWIN).ingest(p, kind="terraform_plan").to_dict()["changes"]
    assert [c["resource_id"] for c in changes] == ["aws:security-group:web", "aws:security-group:web"]
    assert coverage(p)["status"] == "complete"
