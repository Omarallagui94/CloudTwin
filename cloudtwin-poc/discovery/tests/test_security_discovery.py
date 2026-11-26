from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from jsonschema import Draft202012Validator

from analysis.models import AnalysisContext, Change, ChangeSource, Twin
from analysis.rules.resilience.res_002_rds_multi_az import RdsMultiAzRule
from analysis.rules.security.iam_003_escalation_path import EscalationPathRule
from discovery.aws_collector import AWSCollector
from discovery.kubernetes_collector import KubernetesCollector
from discovery.normalizer import normalize

POC = Path(__file__).resolve().parents[2]


class Pages:
    def __init__(self, items):
        self.items = items

    def paginate(self, **_kwargs):
        return self

    def search(self, _expression):
        return iter(self.items)


def metadata(name, namespace=None, uid="uid", labels=None):
    return NS(name=name, namespace=namespace, uid=uid, labels=labels, owner_references=[])


def container(name, privileged=None, *, context=True, init=False):
    security = None
    if context:
        security = NS(
            privileged=privileged,
            run_as_user=1000 if not init else 0,
            run_as_non_root=not init,
            allow_privilege_escalation=False if not init else None,
            capabilities=NS(add=["NET_BIND_SERVICE"], drop=["ALL"]),
        )
    return NS(name=name, image=f"example/{name}:1", security_context=security)


def pod_spec():
    return NS(
        containers=[container("privileged", True), container("unspecified", context=False)],
        init_containers=[container("init", False, init=True)],
        security_context=NS(run_as_user=1000, run_as_non_root=True),
        service_account_name="app",
        node_name="node-1",
    )


def collector_without_config() -> KubernetesCollector:
    collector = KubernetesCollector.__new__(KubernetesCollector)
    collector.warnings = []
    return collector


def test_kubernetes_security_contexts_preserve_true_false_and_unknown():
    spec = pod_spec()
    deployment = NS(
        metadata=metadata("api", "default"),
        spec=NS(template=NS(spec=spec), selector=NS(match_labels={"app": "api"}), replicas=2),
        status=NS(ready_replicas=2),
    )
    collector = collector_without_config()
    collector.apps = NS(list_deployment_for_all_namespaces=Mock(return_value=NS(
        items=[deployment], metadata=NS(_continue=None)
    )))

    [result] = collector.collect_deployments()
    contexts = {item["name"]: item["security_context"] for item in result["containers"]}
    assert contexts["privileged"]["privileged"] is True
    assert contexts["unspecified"] is None
    assert result["init_containers"][0]["security_context"]["privileged"] is False
    assert result["pod_security_context"] == {"run_as_user": 1000, "run_as_non_root": True}


def test_cluster_role_bindings_collect_subjects_and_paginate():
    role = NS(metadata=metadata("cluster-admin"), rules=[{"verbs": ["*"]}])
    binding = NS(
        metadata=metadata("admins"),
        role_ref={"api_group": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
        subjects=[{"kind": "ServiceAccount", "name": "app", "namespace": "default"}],
    )
    collector = collector_without_config()
    collector.rbac = NS(
        list_cluster_role=Mock(return_value=NS(items=[role], metadata=NS(_continue=None))),
        list_cluster_role_binding=Mock(return_value=NS(items=[binding], metadata=NS(_continue=None))),
    )
    with patch("discovery.kubernetes_collector.client.ApiClient") as api_client:
        api_client.return_value.sanitize_for_serialization.side_effect = lambda value: value
        roles = collector.collect_cluster_roles()
        bindings = collector.collect_cluster_role_bindings()

    assert roles[0]["rules"] == [{"verbs": ["*"]}]
    assert bindings[0]["role_ref"]["name"] == "cluster-admin"
    assert bindings[0]["subjects"][0]["namespace"] == "default"
    assert collector.rbac.list_cluster_role.call_args.kwargs["limit"] == 500


def test_rds_discovery_pagination_optional_fields_and_normalization():
    first = {
        "DBInstanceIdentifier": "orders",
        "DBInstanceArn": "arn:aws:rds:eu-west-3:123:db:orders",
        "Engine": "postgres", "EngineVersion": "16", "DBInstanceClass": "db.t4g.micro",
        "MultiAZ": True, "PubliclyAccessible": False,
        "DBSubnetGroup": {"DBSubnetGroupName": "private", "VpcId": "vpc-1",
                          "Subnets": [{"SubnetIdentifier": "subnet-1"}]},
        "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-1"}],
        "AvailabilityZone": "eu-west-3a", "StorageEncrypted": True, "KmsKeyId": "key-1",
    }
    second = {"DBInstanceIdentifier": "catalog", "MultiAZ": False}
    third = {"DBInstanceIdentifier": "unknown", "MultiAZ": None}
    collector = AWSCollector.__new__(AWSCollector)
    collector.warnings = []
    collector.rds = NS(get_paginator=Mock(return_value=Pages([first, second, third])))

    instances = collector.collect_rds_instances()
    assert [item["db_instance_identifier"] for item in instances] == [
        "orders", "catalog", "unknown"
    ]
    assert instances[0]["multi_az"] is True
    assert instances[1]["multi_az"] is False
    assert instances[2]["multi_az"] is None and instances[2]["kms_key_id"] is None

    twin = normalize({"rds_instances": instances}, {}, "eu-west-3", "retail-store")
    resources = {item["id"]: item for item in twin["resources"]}
    assert resources["aws:rds-instance:orders"]["properties"]["multi_az"] is True
    assert resources["aws:rds-instance:catalog"]["properties"]["multi_az"] is False
    assert resources["aws:rds-instance:unknown"]["properties"]["multi_az"] is None
    schema = json.loads((POC / "schema/v1/twin.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(twin)


def test_iam_documents_reach_twin_and_unreadable_document_is_explicit_unknown():
    role = {"RoleName": "app", "Arn": "arn:aws:iam::123:role/app"}
    attached = {"PolicyName": "AdministratorAccess", "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}
    inline_doc = {"Statement": [{"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"}]}
    iam = Mock()
    iam.get_paginator.side_effect = lambda operation: {
        "list_roles": Pages([role]),
        "list_attached_role_policies": Pages([attached]),
        "list_role_policies": Pages(["inline"]),
    }[operation]
    iam.get_policy.return_value = {"Policy": {"DefaultVersionId": "v7"}}
    iam.get_policy_version.return_value = {"PolicyVersion": {"Document": {
        "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]
    }}}
    iam.get_role_policy.return_value = {"PolicyDocument": inline_doc}
    collector = AWSCollector.__new__(AWSCollector)
    collector.iam, collector.warnings = iam, []

    roles = collector.collect_roles()
    twin = normalize({"iam_roles": roles}, {}, "eu-west-3", "retail-store")
    props = twin["resources"][0]["properties"]
    assert props["attached_policies"][0]["document"]["Statement"][0]["Action"] == "*"
    assert props["inline_policies"][0]["document"] == inline_doc
    iam.get_policy_version.assert_called_once_with(
        PolicyArn=attached["PolicyArn"], VersionId="v7"
    )

    iam.get_policy_version.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetPolicyVersion"
    )
    [unknown_role] = collector.collect_roles()
    policy = unknown_role["attached_policies"][0]
    assert policy["document"] is None and policy["document_available"] is False
    assert EscalationPathRule._danger(unknown_role)[1] is True


def test_rds_rule_unknown_multi_az_is_not_evaluated():
    change = Change(
        change_id="rds-create", operation="create", resource_id="aws:rds-instance:new",
        resource_type="RDS_INSTANCE", provider="aws", property_path="properties",
        after={"db_instance_identifier": "new", "multi_az": None},
        source=ChangeSource(kind="terraform_plan"),
    )
    ctx = AnalysisContext(
        schema_version="1.0.0", simulation_id="test", current_twin=Twin(), changes=[change]
    )
    [finding] = RdsMultiAzRule().evaluate(ctx)
    assert finding.status.value == "not_evaluated"
