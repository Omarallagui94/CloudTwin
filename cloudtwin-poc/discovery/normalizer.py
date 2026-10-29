
"""Normalize discovered AWS and Kubernetes metadata into a graph-friendly model."""

from __future__ import annotations

from typing import Any


ALLOWED_RELATIONSHIP_TYPES = {
    "CONTAINS",
    "LOCATED_IN",
    "USES",
    "MEMBER_OF",
    "RUNS_ON",
    "SELECTS",
    "EXPOSES",
    "ROUTES_TO",
}


def _resource(
    resource_id: str,
    name: str,
    resource_type: str,
    provider: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": resource_id,
        "name": name,
        "type": resource_type,
        "provider": provider,
        "properties": properties,
    }

def _name(item: dict[str, Any], fallback_key: str) -> str:
    return item.get("tags", {}).get("Name") or item.get(fallback_key) or "unknown"


def _matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return bool(selector) and all(
        labels.get(key) == value for key, value in selector.items()
    )


def normalize(
    aws: dict[str, Any],
    kubernetes: dict[str, Any],
    region: str,
    cluster_name: str,
) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = []
    resource_ids: set[str] = set()
    relationship_keys: set[tuple[str, str, str]] = set()

    def add_resource(item: dict[str, Any]) -> None:
        resource_id = item["id"]

        if resource_id in resource_ids:
            raise ValueError(f"Duplicate resource ID: {resource_id}")

        resources.append(item)
        resource_ids.add(resource_id)

    def relate(source: str, target: str, relationship_type: str) -> None:
        if relationship_type not in ALLOWED_RELATIONSHIP_TYPES:
            raise ValueError(
                f"Invalid relationship type: {relationship_type}"
            )

        key = (source, target, relationship_type)

        # Some discovered resources may refer to infrastructure that was
        # not collected. Preserve the existing behavior of skipping those
        # relationships; final snapshot validation checks graph integrity.
        if source not in resource_ids or target not in resource_ids:
            return

        if key not in relationship_keys:
            relationships.append({
                "source": source,
                "target": target,
                "type": relationship_type,
            })
            relationship_keys.add(key)

    # AWS resources
    for item in aws.get("vpcs", []):
        add_resource(_resource(
            f"aws:vpc:{item['vpc_id']}",
            _name(item, "vpc_id"),
            "VPC",
            "aws",
            item,
        ))

    for item in aws.get("subnets", []):
        add_resource(_resource(
            f"aws:subnet:{item['subnet_id']}",
            _name(item, "subnet_id"),
            "SUBNET",
            "aws",
            item,
        ))

    for item in aws.get("security_groups", []):
        add_resource(_resource(
            f"aws:security-group:{item['group_id']}",
            item.get("name") or item["group_id"],
            "SECURITY_GROUP",
            "aws",
            item,
        ))

    for item in aws.get("instances", []):
        add_resource(_resource(
            f"aws:ec2:{item['instance_id']}",
            _name(item, "instance_id"),
            "EC2_INSTANCE",
            "aws",
            item,
        ))

    for item in aws.get("clusters", []):
        add_resource(_resource(
            f"aws:eks:{item['name']}",
            item["name"],
            "EKS_CLUSTER",
            "aws",
            item,
        ))

    for item in aws.get("nodegroups", []):
        add_resource(_resource(
            f"aws:eks-nodegroup:{item['cluster_name']}:{item['name']}",
            item["name"],
            "EKS_NODE_GROUP",
            "aws",
            item,
        ))

    for item in aws.get("load_balancers", []):
        key = item.get("arn") or item["name"]
        add_resource(_resource(
            f"aws:load-balancer:{key}",
            item["name"],
            "LOAD_BALANCER",
            "aws",
            item,
        ))

    for item in aws.get("iam_roles", []):
        add_resource(_resource(
            f"aws:iam-role:{item['arn']}",
            item["name"],
            "IAM_ROLE",
            "aws",
            item,
        ))

    public_lbs = [
        item
        for item in aws.get("load_balancers", [])
        if item.get("scheme") == "internet-facing"
    ]

    if public_lbs:
        add_resource(_resource(
            "aws:internet",
            "Internet",
            "INTERNET",
            "aws",
            {"derived_from": "internet-facing load balancer"},
        ))

    # AWS relationships
    for item in aws.get("subnets", []):
        relate(
            f"aws:vpc:{item['vpc_id']}",
            f"aws:subnet:{item['subnet_id']}",
            "CONTAINS",
        )

    for item in aws.get("security_groups", []):
        if item.get("vpc_id"):
            relate(
                f"aws:vpc:{item['vpc_id']}",
                f"aws:security-group:{item['group_id']}",
                "CONTAINS",
            )

    for item in aws.get("instances", []):
        instance_id = f"aws:ec2:{item['instance_id']}"

        if item.get("subnet_id"):
            relate(
                instance_id,
                f"aws:subnet:{item['subnet_id']}",
                "LOCATED_IN",
            )

        for sg_id in item.get("security_group_ids", []):
            relate(instance_id, f"aws:security-group:{sg_id}", "USES")

        nodegroup = item.get("tags", {}).get("eks:nodegroup-name")
        cluster = (
            item.get("tags", {}).get("kubernetes.io/cluster/retail-store")
            and cluster_name
        )

        if nodegroup:
            relate(
                instance_id,
                f"aws:eks-nodegroup:{cluster or cluster_name}:{nodegroup}",
                "MEMBER_OF",
            )

    for item in aws.get("clusters", []):
        cluster_id = f"aws:eks:{item['name']}"

        if item.get("vpc_id"):
            relate(
                cluster_id,
                f"aws:vpc:{item['vpc_id']}",
                "LOCATED_IN",
            )

        if item.get("role_arn"):
            relate(
                cluster_id,
                f"aws:iam-role:{item['role_arn']}",
                "USES",
            )

        for sg_id in item.get("security_group_ids", []):
            relate(cluster_id, f"aws:security-group:{sg_id}", "USES")

    for item in aws.get("nodegroups", []):
        nodegroup_id = (
            f"aws:eks-nodegroup:{item['cluster_name']}:{item['name']}"
        )

        relate(
            f"aws:eks:{item['cluster_name']}",
            nodegroup_id,
            "CONTAINS",
        )

        if item.get("node_role"):
            relate(
                nodegroup_id,
                f"aws:iam-role:{item['node_role']}",
                "USES",
            )

        for subnet_id in item.get("subnet_ids", []):
            relate(
                nodegroup_id,
                f"aws:subnet:{subnet_id}",
                "LOCATED_IN",
            )

    for item in aws.get("load_balancers", []):
        lb_id = f"aws:load-balancer:{item.get('arn') or item['name']}"

        if item.get("vpc_id"):
            relate(lb_id, f"aws:vpc:{item['vpc_id']}", "LOCATED_IN")

        for subnet_id in item.get("subnet_ids", []):
            relate(lb_id, f"aws:subnet:{subnet_id}", "LOCATED_IN")

        for sg_id in item.get("security_group_ids", []):
            relate(lb_id, f"aws:security-group:{sg_id}", "USES")

        if item.get("scheme") == "internet-facing":
            relate("aws:internet", lb_id, "EXPOSES")

    # Kubernetes resource types and ID slugs
    kinds = {
        "namespaces": "NAMESPACE",
        "nodes": "NODE",
        "deployments": "DEPLOYMENT",
        "pods": "POD",
        "services": "SERVICE",
        "statefulsets": "STATEFUL_SET",
        "service_accounts": "SERVICE_ACCOUNT",
        "ingresses": "INGRESS",
        "network_policies": "NETWORK_POLICY",
    }

    singular = {
        "NAMESPACE": "namespace",
        "NODE": "node",
        "DEPLOYMENT": "deployment",
        "POD": "pod",
        "SERVICE": "service",
        "STATEFUL_SET": "statefulset",
        "SERVICE_ACCOUNT": "service-account",
        "INGRESS": "ingress",
        "NETWORK_POLICY": "network-policy",
    }

    for collection, resource_type in kinds.items():
        for item in kubernetes.get(collection, []):
            namespace = item.get("namespace")
            suffix = (
                f"{namespace}:{item['name']}"
                if namespace
                else item["name"]
            )

            add_resource(_resource(
                f"k8s:{singular[resource_type]}:{suffix}",
                item["name"],
                resource_type,
                "kubernetes",
                item,
            ))

    # Kubernetes relationships
    eks_id = f"aws:eks:{cluster_name}"

    for namespace in kubernetes.get("namespaces", []):
        relate(
            eks_id,
            f"k8s:namespace:{namespace['name']}",
            "CONTAINS",
        )

    for node in kubernetes.get("nodes", []):
        node_id = f"k8s:node:{node['name']}"
        relate(eks_id, node_id, "CONTAINS")

        provider_id = node.get("provider_id") or ""
        instance_id = provider_id.rsplit("/", 1)[-1]

        if instance_id.startswith("i-"):
            relate(node_id, f"aws:ec2:{instance_id}", "RUNS_ON")

    namespaced_collections = [
        "deployments",
        "pods",
        "services",
        "statefulsets",
        "service_accounts",
        "ingresses",
        "network_policies",
    ]

    for collection in namespaced_collections:
        resource_type = kinds[collection]

        for item in kubernetes.get(collection, []):
            resource_id = (
                f"k8s:{singular[resource_type]}:"
                f"{item['namespace']}:{item['name']}"
            )

            relate(
                f"k8s:namespace:{item['namespace']}",
                resource_id,
                "CONTAINS",
            )

    pods = kubernetes.get("pods", [])

    workloads = (
        [("deployment", item) for item in kubernetes.get("deployments", [])]
        + [
            ("statefulset", item)
            for item in kubernetes.get("statefulsets", [])
        ]
    )

    for pod in pods:
        pod_id = f"k8s:pod:{pod['namespace']}:{pod['name']}"

        if pod.get("node_name"):
            relate(
                pod_id,
                f"k8s:node:{pod['node_name']}",
                "RUNS_ON",
            )

        if pod.get("service_account"):
            relate(
                pod_id,
                f"k8s:service-account:{pod['namespace']}:{pod['service_account']}",
                "USES",
            )

    for kind, workload in workloads:
        workload_id = (
            f"k8s:{kind}:{workload['namespace']}:{workload['name']}"
        )

        if workload.get("service_account"):
            relate(
                workload_id,
                f"k8s:service-account:{workload['namespace']}:{workload['service_account']}",
                "USES",
            )

        for pod in pods:
            if (
                pod["namespace"] == workload["namespace"]
                and _matches(workload.get("selector", {}), pod.get("labels", {}))
            ):
                relate(
                    workload_id,
                    f"k8s:pod:{pod['namespace']}:{pod['name']}",
                    "SELECTS",
                )

    for service in kubernetes.get("services", []):
        service_id = (
            f"k8s:service:{service['namespace']}:{service['name']}"
        )
        selected_workloads: set[str] = set()

        for pod in pods:
            if (
                pod["namespace"] == service["namespace"]
                and _matches(service.get("selector", {}), pod.get("labels", {}))
            ):
                relate(
                    service_id,
                    f"k8s:pod:{pod['namespace']}:{pod['name']}",
                    "SELECTS",
                )

        for kind, workload in workloads:
            if (
                workload["namespace"] == service["namespace"]
                and service.get("selector")
                and service["selector"] == workload.get("selector")
            ):
                selected_workloads.add(
                    f"k8s:{kind}:{workload['namespace']}:{workload['name']}"
                )

        for workload_id in selected_workloads:
            relate(service_id, workload_id, "EXPOSES")

        for endpoint in service.get("external_endpoints", []):
            endpoint = endpoint.rstrip(".").lower()

            for lb in aws.get("load_balancers", []):
                lb_endpoint = (lb.get("dns_name") or "").rstrip(".").lower()

                if lb_endpoint == endpoint:
                    relate(
                        f"aws:load-balancer:{lb.get('arn') or lb['name']}",
                        service_id,
                        "ROUTES_TO",
                    )

    for ingress in kubernetes.get("ingresses", []):
        ingress_id = (
            f"k8s:ingress:{ingress['namespace']}:{ingress['name']}"
        )

        for service_name in ingress.get("backend_services", []):
            relate(
                ingress_id,
                f"k8s:service:{ingress['namespace']}:{service_name}",
                "ROUTES_TO",
            )

    for policy in kubernetes.get("network_policies", []):
        policy_id = (
            f"k8s:network-policy:{policy['namespace']}:{policy['name']}"
        )

        for pod in pods:
            if (
                pod["namespace"] == policy["namespace"]
                and _matches(
                    policy.get("pod_selector", {}),
                    pod.get("labels", {}),
                )
            ):
                relate(
                    policy_id,
                    f"k8s:pod:{pod['namespace']}:{pod['name']}",
                    "SELECTS",
                )

    # Snapshot summary and metadata
    aws_count = sum(1 for item in resources if item["provider"] == "aws")
    kubernetes_count = sum(
        1 for item in resources if item["provider"] == "kubernetes"
    )

    warnings = list(aws.get("warnings", [])) + list(
        kubernetes.get("warnings", [])
    )

    return {
        "schema_version": "1.0.0",
        "metadata": {
            "provider": "AWS",
            "region": region,
            "cluster": cluster_name,
        },
        "resources": resources,
        "relationships": relationships,
        "summary": {
            "aws_resources": aws_count,
            "kubernetes_resources": kubernetes_count,
            "relationships": len(relationships),
        },
        "warnings": warnings,
    }
