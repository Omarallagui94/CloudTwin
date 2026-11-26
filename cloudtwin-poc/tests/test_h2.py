
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from kubernetes.client.exceptions import ApiException

from discovery.aws_collector import AWSCollector
from discovery.kubernetes_collector import KubernetesCollector
from discovery.normalizer import normalize

FIXTURE = ROOT / "fixtures" / "sample_current_state.json"


AWS_COLLECTIONS = {
    "VPC": "vpcs",
    "SUBNET": "subnets",
    "SECURITY_GROUP": "security_groups",
    "EC2_INSTANCE": "instances",
    "EKS_CLUSTER": "clusters",
    "EKS_NODE_GROUP": "nodegroups",
    "LOAD_BALANCER": "load_balancers",
    "IAM_ROLE": "iam_roles",
}

KUBERNETES_COLLECTIONS = {
    "NAMESPACE": "namespaces",
    "NODE": "nodes",
    "DEPLOYMENT": "deployments",
    "POD": "pods",
    "SERVICE": "services",
    "STATEFUL_SET": "statefulsets",
    "SERVICE_ACCOUNT": "service_accounts",
    "INGRESS": "ingresses",
    "NETWORK_POLICY": "network_policies",
}


def reconstruct_collector_inputs(twin):
    """Reconstruct collector-shaped inputs from saved resource properties."""
    aws = {
        key: [] for key in AWS_COLLECTIONS.values()
    }
    kubernetes = {
        key: [] for key in KUBERNETES_COLLECTIONS.values()
    }

    for resource in twin["resources"]:
        resource_type = resource["type"]
        properties = resource.get("properties", {})

        if resource_type in AWS_COLLECTIONS:
            aws[AWS_COLLECTIONS[resource_type]].append(properties)

        elif resource_type in KUBERNETES_COLLECTIONS:
            kubernetes[KUBERNETES_COLLECTIONS[resource_type]].append(
                properties
            )

        elif resource_type == "INTERNET":
            # INTERNET is derived by normalize(), not collected directly.
            continue

        else:
            raise AssertionError(
                f"Unrecognized resource type in fixture: {resource_type}"
            )

    aws["warnings"] = [
        warning
        for warning in twin.get("warnings", [])
        if isinstance(warning, str)
    ]
    kubernetes["warnings"] = []

    return aws, kubernetes


class H2FixtureTests(unittest.TestCase):
    def test_saved_snapshot_counts_and_relationships(self):
        twin = json.loads(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(len(twin["resources"]), 40)
        self.assertEqual(len(twin["relationships"]), 81)
        self.assertEqual(twin["summary"]["aws_resources"], 15)
        self.assertEqual(twin["summary"]["kubernetes_resources"], 25)

        resource_ids = {resource["id"] for resource in twin["resources"]}
        self.assertEqual(len(resource_ids), len(twin["resources"]))

        for relationship in twin["relationships"]:
            self.assertIn(relationship["source"], resource_ids)
            self.assertIn(relationship["target"], resource_ids)

    def test_saved_snapshot_round_trip_reproduces_counts_and_graph(self):
        twin = json.loads(FIXTURE.read_text(encoding="utf-8"))
        aws, kubernetes = reconstruct_collector_inputs(twin)

        metadata = twin.get("metadata", {})
        region = metadata.get("region")
        cluster_name = metadata.get("cluster")

        self.assertTrue(region, "Fixture metadata must include a region")
        self.assertTrue(
            cluster_name,
            "Fixture metadata must include a cluster name",
        )

        regenerated = normalize(
            aws,
            kubernetes,
            region,
            cluster_name,
        )

        self.assertEqual(len(regenerated["resources"]), 40)
        self.assertEqual(len(regenerated["relationships"]), 81)
        self.assertEqual(regenerated["summary"]["aws_resources"], 15)
        self.assertEqual(
            regenerated["summary"]["kubernetes_resources"], 25
        )

        original_resource_ids = {
            resource["id"] for resource in twin["resources"]
        }
        regenerated_resource_ids = {
            resource["id"] for resource in regenerated["resources"]
        }
        self.assertEqual(
            regenerated_resource_ids,
            original_resource_ids,
        )

        original_relationships = {
            (
                relationship["source"],
                relationship["target"],
                relationship["type"],
            )
            for relationship in twin["relationships"]
        }
        regenerated_relationships = {
            (
                relationship["source"],
                relationship["target"],
                relationship["type"],
            )
            for relationship in regenerated["relationships"]
        }
        self.assertEqual(
            regenerated_relationships,
            original_relationships,
        )


class H2CollectorTests(unittest.TestCase):
    def test_aws_vpc_pagination_collects_multiple_pages(self):
        collector = AWSCollector.__new__(AWSCollector)
        collector.warnings = []
        collector.ec2 = MagicMock()

        paginator = MagicMock()
        collector.ec2.get_paginator.return_value = paginator
        paginator.paginate.return_value.search.return_value = iter([
            {"VpcId": "vpc-1", "Tags": []},
            {"VpcId": "vpc-2", "Tags": []},
        ])

        result = collector.collect_vpcs()

        self.assertEqual(
            [item["vpc_id"] for item in result],
            ["vpc-1", "vpc-2"],
        )
        self.assertEqual(collector.warnings, [])

    def test_aws_api_failure_returns_empty_result_and_warning(self):
        collector = AWSCollector.__new__(AWSCollector)
        collector.warnings = []

        def fail():
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Denied"}},
                "DescribeVpcs",
            )

        result = collector._safe("VPC discovery", fail)

        self.assertEqual(result, [])
        self.assertTrue(
            any(
                "VPC discovery" in warning
                for warning in collector.warnings
            )
        )

    def test_aws_security_groups_include_ingress_and_egress_rules(self):
        collector = AWSCollector.__new__(AWSCollector)
        collector.warnings = []
        collector.ec2 = MagicMock()

        ingress = [
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "IpRanges": [{"CidrIp": "10.0.0.0/8"}],
            }
        ]
        egress = [
            {
                "IpProtocol": "-1",
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }
        ]

        page = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-123",
                    "GroupName": "web",
                    "Description": "web access",
                    "VpcId": "vpc-123",
                    "IpPermissions": ingress,
                    "IpPermissionsEgress": egress,
                    "Tags": [],
                }
            ]
        }

        paginator = MagicMock()
        paginator.paginate.return_value.search.return_value = iter(
            page["SecurityGroups"]
        )
        collector.ec2.get_paginator.return_value = paginator

        result = collector.collect_security_groups()

        self.assertEqual(len(result), 1)

        group = result[0]
        self.assertEqual(group["ingress_rules"], ingress)
        self.assertEqual(group["egress_rules"], egress)
        self.assertEqual(group["ingress_rule_count"], 1)
        self.assertEqual(group["egress_rule_count"], 1)
        self.assertEqual(collector.warnings, [])

    def test_aws_iam_roles_include_attached_and_inline_policy_documents(self):
        collector = AWSCollector.__new__(AWSCollector)
        collector.warnings = []
        collector.iam = MagicMock()

        managed_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": ["arn:aws:s3:::example-bucket/*"],
                }
            ],
        }

        inline_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogStream"],
                    "Resource": ["*"],
                }
            ],
        }

        role = {
            "RoleName": "CloudTwinTestRole",
            "Arn": "arn:aws:iam::123456789012:role/CloudTwinTestRole",
            "Path": "/",
            "Description": "Test role",
            "MaxSessionDuration": 3600,
        }

        attached_policy = {
            "PolicyName": "ReadExampleBucket",
            "PolicyArn": (
                "arn:aws:iam::123456789012:policy/ReadExampleBucket"
            ),
        }

        def make_paginator(name):
            paginator = MagicMock()

            if name == "list_roles":
                paginator.paginate.return_value.search.return_value = iter(
                    [role]
                )
            elif name == "list_attached_role_policies":
                paginator.paginate.return_value.search.return_value = iter(
                    [attached_policy]
                )
            elif name == "list_role_policies":
                paginator.paginate.return_value.search.return_value = iter(
                    ["InlineLoggingPolicy"]
                )
            else:
                raise AssertionError(
                    f"Unexpected IAM paginator: {name}"
                )

            return paginator

        collector.iam.get_paginator.side_effect = make_paginator
        collector.iam.get_policy.return_value = {
            "Policy": {"DefaultVersionId": "v1"}
        }
        collector.iam.get_policy_version.return_value = {
            "PolicyVersion": {"Document": managed_document}
        }
        collector.iam.get_role_policy.return_value = {
            "PolicyDocument": inline_document
        }

        result = collector.collect_roles()

        self.assertEqual(len(result), 1)
        collected_role = result[0]
        self.assertEqual(collected_role["name"], "CloudTwinTestRole")

        self.assertEqual(len(collected_role["attached_policies"]), 1)
        self.assertEqual(
            collected_role["attached_policies"][0]["name"],
            "ReadExampleBucket",
        )
        self.assertEqual(
            collected_role["attached_policies"][0]["document"],
            managed_document,
        )

        self.assertEqual(len(collected_role["inline_policies"]), 1)
        self.assertEqual(
            collected_role["inline_policies"][0]["name"],
            "InlineLoggingPolicy",
        )
        self.assertEqual(
            collected_role["inline_policies"][0]["document"],
            inline_document,
        )

        self.assertEqual(collector.warnings, [])

    def test_kubernetes_api_failure_returns_empty_result_and_warning(self):
        collector = KubernetesCollector.__new__(KubernetesCollector)
        collector.warnings = []

        def fail():
            raise ApiException(status=403, reason="Forbidden")

        result = collector._safe("Pod discovery", fail)

        self.assertEqual(result, [])
        self.assertTrue(
            any(
                "Pod discovery" in warning and "403" in warning
                for warning in collector.warnings
            )
        )

    def test_kubernetes_pagination_collects_multiple_pages(self):
        collector = KubernetesCollector.__new__(KubernetesCollector)

        first_page = SimpleNamespace(
            items=["namespace-a", "namespace-b"],
            metadata=SimpleNamespace(_continue="next-token"),
        )
        second_page = SimpleNamespace(
            items=["namespace-c"],
            metadata=SimpleNamespace(_continue=""),
        )

        operation = MagicMock(side_effect=[first_page, second_page])

        result = collector._list_all(operation)

        self.assertEqual(
            result,
            ["namespace-a", "namespace-b", "namespace-c"],
        )
        self.assertEqual(operation.call_count, 2)
        operation.assert_any_call(limit=500, _continue=None)
        operation.assert_any_call(
            limit=500,
            _continue="next-token",
        )

    def test_kubernetes_network_policy_preserves_ingress_and_egress_rules(self):
        collector = KubernetesCollector.__new__(KubernetesCollector)
        collector.warnings = []
        collector.networking = MagicMock()

        ingress_rules = [
            {
                "from": [
                    {"podSelector": {"matchLabels": {"app": "frontend"}}}
                ],
                "ports": [{"protocol": "TCP", "port": 8080}],
            }
        ]

        egress_rules = [
            {
                "to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            }
        ]

        network_policy = SimpleNamespace(
            metadata=SimpleNamespace(
                name="restrict-traffic",
                namespace="default",
                uid="policy-123",
                labels={"team": "platform"},
            ),
            spec=SimpleNamespace(
                pod_selector=SimpleNamespace(
                    match_labels={"app": "backend"}
                ),
                policy_types=["Ingress", "Egress"],
                ingress=ingress_rules,
                egress=egress_rules,
            ),
        )

        collector.networking.list_network_policy_for_all_namespaces.return_value = (
            SimpleNamespace(items=[network_policy])
        )

        with patch(
            "discovery.kubernetes_collector.client.ApiClient"
        ) as api_client_class:
            api_client_class.return_value.sanitize_for_serialization.side_effect = (
                lambda value: value
            )

            result = collector.collect_network_policies()

        self.assertEqual(len(result), 1)
        policy = result[0]
        self.assertEqual(policy["name"], "restrict-traffic")
        self.assertEqual(policy["namespace"], "default")
        self.assertEqual(policy["pod_selector"], {"app": "backend"})
        self.assertEqual(policy["policy_types"], ["Ingress", "Egress"])
        self.assertEqual(policy["ingress_rules"], ingress_rules)
        self.assertEqual(policy["egress_rules"], egress_rules)
        self.assertEqual(collector.warnings, [])


class H2NormalizerTests(unittest.TestCase):
    def test_normalization_is_deterministic(self):
        aws = {
            "vpcs": [
                {
                    "vpc_id": "vpc-1",
                    "cidr": "10.0.0.0/16",
                    "state": "available",
                    "is_default": False,
                    "tags": {},
                }
            ],
            "subnets": [],
            "security_groups": [],
            "instances": [],
            "clusters": [],
            "nodegroups": [],
            "load_balancers": [],
            "iam_roles": [],
            "warnings": [],
        }

        kubernetes = {
            "namespaces": [{"name": "default"}],
            "nodes": [],
            "deployments": [],
            "pods": [],
            "services": [],
            "statefulsets": [],
            "service_accounts": [],
            "ingresses": [],
            "network_policies": [],
            "warnings": [],
        }

        first = normalize(
            aws, kubernetes, "eu-west-3", "retail-store"
        )
        second = normalize(
            aws, kubernetes, "eu-west-3", "retail-store"
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first["resources"]), 2)
        self.assertEqual(first["summary"]["aws_resources"], 1)
        self.assertEqual(first["summary"]["kubernetes_resources"], 1)
        self.assertEqual(first["summary"]["relationships"], 0)


if __name__ == "__main__":
    unittest.main()
