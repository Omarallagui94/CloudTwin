"""Read-only AWS infrastructure discovery for the CloudTwin proof of concept."""

from __future__ import annotations

from typing import Any, Callable

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class AWSCollector:
    """Collect selected AWS resources using Boto3's default credential chain."""

    def __init__(self, region: str = "eu-west-3") -> None:
        self.region = region
        self.session = boto3.Session(region_name=region)
        config = Config(
            retries={"total_max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=20,
        )
        self.ec2 = self.session.client("ec2", config=config)
        self.eks = self.session.client("eks", config=config)
        self.elbv2 = self.session.client("elbv2", config=config)
        self.elb = self.session.client("elb", config=config)
        self.iam = self.session.client("iam", config=config)
        self.warnings: list[str] = []

    def _safe(self, label: str, operation: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        try:
            return operation()
        except (ClientError, BotoCoreError) as exc:
            self.warnings.append(f"{label}: {exc}")
            return []

    @staticmethod
    def _tags(tags: list[dict[str, str]] | None) -> dict[str, str]:
        return {tag["Key"]: tag["Value"] for tag in tags or []}

    def collect_vpcs(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            paginator = self.ec2.get_paginator("describe_vpcs")
            for vpc in paginator.paginate().search("Vpcs[]"):
                result.append({
                    "vpc_id": vpc["VpcId"], "cidr": vpc.get("CidrBlock"),
                    "state": vpc.get("State"), "is_default": vpc.get("IsDefault", False),
                    "tags": self._tags(vpc.get("Tags")),
                })
            return result
        return self._safe("VPC discovery", operation)

    def collect_subnets(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for item in self.ec2.get_paginator("describe_subnets").paginate().search("Subnets[]"):
                result.append({
                    "subnet_id": item["SubnetId"], "vpc_id": item["VpcId"],
                    "cidr": item.get("CidrBlock"), "availability_zone": item.get("AvailabilityZone"),
                    "map_public_ip": item.get("MapPublicIpOnLaunch", False),
                    "tags": self._tags(item.get("Tags")),
                })
            return result
        return self._safe("Subnet discovery", operation)


    def collect_security_groups(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            paginator = self.ec2.get_paginator("describe_security_groups")

            for item in paginator.paginate().search("SecurityGroups[]"):
                ingress = item.get("IpPermissions", [])
                egress = item.get("IpPermissionsEgress", [])

                result.append({
                    "group_id": item["GroupId"],
                    "name": item.get("GroupName"),
                    "description": item.get("Description"),
                    "vpc_id": item.get("VpcId"),
                    "ingress_rule_count": len(ingress),
                    "egress_rule_count": len(egress),
                    "ingress_rules": ingress,
                    "egress_rules": egress,
                    "tags": self._tags(item.get("Tags")),
                })

            return result

        return self._safe("Security Group discovery", operation)


    def collect_instances(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            pages = self.ec2.get_paginator("describe_instances").paginate()
            for reservation in pages.search("Reservations[]"):
                for item in reservation.get("Instances", []):
                    result.append({
                        "instance_id": item["InstanceId"], "instance_type": item.get("InstanceType"),
                        "state": item.get("State", {}).get("Name"), "vpc_id": item.get("VpcId"),
                        "subnet_id": item.get("SubnetId"), "private_ip": item.get("PrivateIpAddress"),
                        "availability_zone": item.get("Placement", {}).get("AvailabilityZone"),
                        "security_group_ids": [sg["GroupId"] for sg in item.get("SecurityGroups", [])],
                        "iam_instance_profile_arn": item.get("IamInstanceProfile", {}).get("Arn"),
                        "tags": self._tags(item.get("Tags")),
                    })
            return result
        return self._safe("EC2 instance discovery", operation)

    def collect_clusters(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for name in self.eks.get_paginator("list_clusters").paginate().search("clusters[]"):
                cluster = self.eks.describe_cluster(name=name)["cluster"]
                vpc = cluster.get("resourcesVpcConfig", {})
                result.append({
                    "name": name, "arn": cluster.get("arn"), "status": cluster.get("status"),
                    "version": cluster.get("version"), "role_arn": cluster.get("roleArn"),
                    "vpc_id": vpc.get("vpcId"), "subnet_ids": vpc.get("subnetIds", []),
                    "security_group_ids": list({*(vpc.get("securityGroupIds", [])), *([vpc["clusterSecurityGroupId"]] if vpc.get("clusterSecurityGroupId") else [])}),
                    "endpoint_public_access": vpc.get("endpointPublicAccess"),
                    "endpoint_private_access": vpc.get("endpointPrivateAccess"),
                    "tags": cluster.get("tags", {}),
                })
            return result
        return self._safe("EKS cluster discovery", operation)

    def collect_nodegroups(self, clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for cluster in clusters:
                names = []
                paginator = self.eks.get_paginator("list_nodegroups")
                for page in paginator.paginate(clusterName=cluster["name"]):
                    names.extend(page.get("nodegroups", []))
                for name in names:
                    item = self.eks.describe_nodegroup(clusterName=cluster["name"], nodegroupName=name)["nodegroup"]
                    scaling = item.get("scalingConfig", {})
                    result.append({
                        "name": name, "cluster_name": cluster["name"], "arn": item.get("nodegroupArn"),
                        "status": item.get("status"), "node_role": item.get("nodeRole"),
                        "subnet_ids": item.get("subnets", []), "instance_types": item.get("instanceTypes", []),
                        "desired_size": scaling.get("desiredSize"), "min_size": scaling.get("minSize"),
                        "max_size": scaling.get("maxSize"), "tags": item.get("tags", {}),
                    })
            return result
        return self._safe("EKS node group discovery", operation)

    def collect_load_balancers(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for item in self.elbv2.get_paginator("describe_load_balancers").paginate().search("LoadBalancers[]"):
                result.append({
                    "arn": item["LoadBalancerArn"], "name": item["LoadBalancerName"],
                    "dns_name": item.get("DNSName"), "scheme": item.get("Scheme"),
                    "type": item.get("Type"), "state": item.get("State", {}).get("Code"),
                    "vpc_id": item.get("VpcId"), "security_group_ids": item.get("SecurityGroups", []),
                    "subnet_ids": [zone["SubnetId"] for zone in item.get("AvailabilityZones", [])],
                })
            for page in self.elb.get_paginator("describe_load_balancers").paginate():
                for item in page.get("LoadBalancerDescriptions", []):
                    result.append({
                        "name": item["LoadBalancerName"], "dns_name": item.get("DNSName"),
                        "scheme": item.get("Scheme"), "type": "classic", "state": "active",
                        "vpc_id": item.get("VPCId"), "security_group_ids": item.get("SecurityGroups", []),
                        "subnet_ids": item.get("Subnets", []),
                    })
            return result
        return self._safe("Load Balancer discovery", operation)


    def collect_roles(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            paginator = self.iam.get_paginator("list_roles")

            for role in paginator.paginate().search("Roles[]"):
                role_data = {
                    "name": role["RoleName"],
                    "arn": role["Arn"],
                    "path": role.get("Path"),
                    "description": role.get("Description"),
                    "max_session_duration": role.get("MaxSessionDuration"),
                    "attached_policies": [],
                    "inline_policies": [],
                }

                role_name = role["RoleName"]

                # Attached managed policies
                try:
                    attached_paginator = self.iam.get_paginator(
                        "list_attached_role_policies"
                    )
                    for policy in attached_paginator.paginate(
                        RoleName=role_name
                    ).search("AttachedPolicies[]"):
                        policy_data = {
                            "name": policy["PolicyName"],
                            "arn": policy["PolicyArn"],
                        }

                        try:
                            details = self.iam.get_policy(
                                PolicyArn=policy["PolicyArn"]
                            )["Policy"]
                            version_id = details["DefaultVersionId"]
                            version = self.iam.get_policy_version(
                                PolicyArn=policy["PolicyArn"],
                                VersionId=version_id,
                            )["PolicyVersion"]
                            policy_data["document"] = version.get(
                                "Document", {}
                            )
                        except (ClientError, BotoCoreError, KeyError) as exc:
                            self.warnings.append(
                                f"IAM managed policy {policy['PolicyArn']}: {exc}"
                            )

                        role_data["attached_policies"].append(policy_data)

                except (ClientError, BotoCoreError) as exc:
                    self.warnings.append(
                        f"IAM attached policies for role {role_name}: {exc}"
                    )

                # Inline policies
                try:
                    inline_paginator = self.iam.get_paginator("list_role_policies")
                    for policy_name in inline_paginator.paginate(
                        RoleName=role_name
                    ).search("PolicyNames[]"):
                        policy_data = {"name": policy_name}

                        try:
                            policy_data["document"] = self.iam.get_role_policy(
                                RoleName=role_name,
                                PolicyName=policy_name,
                            ).get("PolicyDocument", {})
                        except (ClientError, BotoCoreError) as exc:
                            self.warnings.append(
                                f"IAM inline policy {role_name}/{policy_name}: {exc}"
                            )

                        role_data["inline_policies"].append(policy_data)

                except (ClientError, BotoCoreError) as exc:
                    self.warnings.append(
                        f"IAM inline policies for role {role_name}: {exc}"
                    )

                result.append(role_data)

            return result

        return self._safe("IAM role discovery", operation)


    def collect(self) -> dict[str, Any]:
        clusters = self.collect_clusters()
        return {
            "vpcs": self.collect_vpcs(), "subnets": self.collect_subnets(),
            "security_groups": self.collect_security_groups(), "instances": self.collect_instances(),
            "clusters": clusters, "nodegroups": self.collect_nodegroups(clusters),
            "load_balancers": self.collect_load_balancers(), "iam_roles": self.collect_roles(),
            "warnings": self.warnings,
        }
