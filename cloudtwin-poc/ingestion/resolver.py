"""Canonical resource ID resolution against Current Twin snapshots."""

from __future__ import annotations

import re
from typing import Any

from ingestion.models import UnresolvedTargetError

# Allowed resource types from twin.schema.json
VALID_RESOURCE_TYPES = frozenset({
    "INTERNET",
    "VPC",
    "SUBNET",
    "SECURITY_GROUP",
    "EC2_INSTANCE",
    "EKS_CLUSTER",
    "EKS_NODE_GROUP",
    "LOAD_BALANCER",
    "IAM_ROLE",
    "NAMESPACE",
    "NODE",
    "DEPLOYMENT",
    "POD",
    "SERVICE",
    "STATEFUL_SET",
    "SERVICE_ACCOUNT",
    "INGRESS",
    "NETWORK_POLICY",
})

KUBERNETES_TYPES = frozenset({
    "NAMESPACE",
    "NODE",
    "DEPLOYMENT",
    "POD",
    "SERVICE",
    "STATEFUL_SET",
    "SERVICE_ACCOUNT",
    "INGRESS",
    "NETWORK_POLICY",
})

AWS_TYPES = frozenset({
    "INTERNET",
    "VPC",
    "SUBNET",
    "SECURITY_GROUP",
    "EC2_INSTANCE",
    "EKS_CLUSTER",
    "EKS_NODE_GROUP",
    "LOAD_BALANCER",
    "IAM_ROLE",
})

# Mapping from common aliases / Terraform types / K8s kinds to canonical type & slug
TYPE_NORMALIZATION: dict[str, tuple[str, str, str]] = {
    # format: key.lower() -> (standard_type, provider, slug)
    # Kubernetes
    "deployment": ("DEPLOYMENT", "kubernetes", "deployment"),
    "deployments": ("DEPLOYMENT", "kubernetes", "deployment"),
    "service": ("SERVICE", "kubernetes", "service"),
    "services": ("SERVICE", "kubernetes", "service"),
    "pod": ("POD", "kubernetes", "pod"),
    "pods": ("POD", "kubernetes", "pod"),
    "node": ("NODE", "kubernetes", "node"),
    "nodes": ("NODE", "kubernetes", "node"),
    "namespace": ("NAMESPACE", "kubernetes", "namespace"),
    "namespaces": ("NAMESPACE", "kubernetes", "namespace"),
    # Canonical CloudTwin IDs use the Kubernetes API kind without punctuation.
    "statefulset": ("STATEFUL_SET", "kubernetes", "statefulset"),
    "statefulsets": ("STATEFUL_SET", "kubernetes", "statefulset"),
    "stateful_set": ("STATEFUL_SET", "kubernetes", "statefulset"),
    "serviceaccount": ("SERVICE_ACCOUNT", "kubernetes", "service-account"),
    "serviceaccounts": ("SERVICE_ACCOUNT", "kubernetes", "service-account"),
    "service_account": ("SERVICE_ACCOUNT", "kubernetes", "service-account"),
    "ingress": ("INGRESS", "kubernetes", "ingress"),
    "ingresses": ("INGRESS", "kubernetes", "ingress"),
    "networkpolicy": ("NETWORK_POLICY", "kubernetes", "network-policy"),
    "networkpolicies": ("NETWORK_POLICY", "kubernetes", "network-policy"),
    "network_policy": ("NETWORK_POLICY", "kubernetes", "network-policy"),
    # AWS & Terraform AWS
    "vpc": ("VPC", "aws", "vpc"),
    "aws_vpc": ("VPC", "aws", "vpc"),
    "subnet": ("SUBNET", "aws", "subnet"),
    "aws_subnet": ("SUBNET", "aws", "subnet"),
    "securitygroup": ("SECURITY_GROUP", "aws", "security-group"),
    "security_group": ("SECURITY_GROUP", "aws", "security-group"),
    "aws_security_group": ("SECURITY_GROUP", "aws", "security-group"),
    "ec2": ("EC2_INSTANCE", "aws", "ec2"),
    "ec2_instance": ("EC2_INSTANCE", "aws", "ec2"),
    "aws_instance": ("EC2_INSTANCE", "aws", "ec2"),
    "instance": ("EC2_INSTANCE", "aws", "ec2"),
    "eks": ("EKS_CLUSTER", "aws", "eks"),
    "eks_cluster": ("EKS_CLUSTER", "aws", "eks"),
    "aws_eks_cluster": ("EKS_CLUSTER", "aws", "eks"),
    "nodegroup": ("EKS_NODE_GROUP", "aws", "eks-nodegroup"),
    "eks_node_group": ("EKS_NODE_GROUP", "aws", "eks-nodegroup"),
    "aws_eks_node_group": ("EKS_NODE_GROUP", "aws", "eks-nodegroup"),
    "loadbalancer": ("LOAD_BALANCER", "aws", "load-balancer"),
    "load_balancer": ("LOAD_BALANCER", "aws", "load-balancer"),
    "aws_lb": ("LOAD_BALANCER", "aws", "load-balancer"),
    "aws_alb": ("LOAD_BALANCER", "aws", "load-balancer"),
    "iam_role": ("IAM_ROLE", "aws", "iam-role"),
    "aws_iam_role": ("IAM_ROLE", "aws", "iam-role"),
}

RESOURCE_ID_REGEX = re.compile(r"^(aws:internet|(?:aws|k8s):[a-z][a-z0-9-]*:.+)$")


def normalize_resource_type(raw_type: str) -> tuple[str, str, str]:
    """Normalize input resource type string to (canonical_type, provider, slug).

    Returns:
        tuple of (canonical_type, provider, slug)
    """
    key = raw_type.strip().lower()
    if key in TYPE_NORMALIZATION:
        return TYPE_NORMALIZATION[key]

    # Check if raw_type directly matches canonical enum
    upper = raw_type.strip().upper()
    if upper in VALID_RESOURCE_TYPES:
        provider = "kubernetes" if upper in KUBERNETES_TYPES else "aws"
        slug = upper.lower().replace("_", "-")
        return upper, provider, slug

    raise ValueError(f"Unknown or unsupported resource type: '{raw_type}'")


class CanonicalResourceResolver:
    """Resolves arbitrary change targets against Current Twin resources."""

    def __init__(self, current_twin: dict[str, Any] | None = None) -> None:
        self.resources_by_id: dict[str, dict[str, Any]] = {}
        self.k8s_index: dict[tuple[str, str, str], str] = {}  # (type, namespace, name) -> id
        self.aws_name_index: dict[tuple[str, str], str] = {}  # (type, name) -> id
        self.native_id_index: dict[str, str] = {}  # native_id -> canonical_id

        if current_twin:
            self.index_current_twin(current_twin)

    def index_current_twin(self, twin: dict[str, Any]) -> None:
        """Build indexed lookup tables for all resources in Current Twin."""
        self.resources_by_id.clear()
        self.k8s_index.clear()
        self.aws_name_index.clear()
        self.native_id_index.clear()

        for res in twin.get("resources", []):
            r_id = res["id"]
            r_type = res["type"]
            r_name = res["name"]
            r_prov = res["provider"]
            props = res.get("properties", {})

            self.resources_by_id[r_id] = res

            # Index id suffix (e.g. sg-0aaa..., vpc-0aaa..., i-0aaa...)
            suffix = r_id.split(":")[-1]
            self.native_id_index[suffix] = r_id

            if r_prov == "kubernetes":
                ns = props.get("namespace", "default")
                self.k8s_index[(r_type, ns, r_name)] = r_id
            else:
                self.aws_name_index[(r_type, r_name)] = r_id
                # Index tags.Name if present
                tags = props.get("tags") or {}
                if isinstance(tags, dict) and "Name" in tags:
                    self.aws_name_index[(r_type, tags["Name"])] = r_id

                # Index native identifiers per resource type
                if r_type == "VPC" and props.get("vpc_id"):
                    self.native_id_index[props["vpc_id"]] = r_id
                elif r_type == "SUBNET" and props.get("subnet_id"):
                    self.native_id_index[props["subnet_id"]] = r_id
                elif r_type == "SECURITY_GROUP":
                    sg_id = props.get("group_id") or props.get("security_group_id")
                    if sg_id:
                        self.native_id_index[sg_id] = r_id
                elif r_type == "EC2_INSTANCE" and props.get("instance_id"):
                    self.native_id_index[props["instance_id"]] = r_id
                elif r_type in ("IAM_ROLE", "LOAD_BALANCER"):
                    arn_val = props.get("arn") or props.get("role_arn") or props.get("load_balancer_arn")
                    if arn_val:
                        self.native_id_index[arn_val] = r_id

    def resolve_scenario_target(
        self,
        resource_type_str: str,
        resource_name: str,
        namespace: str | None = None,
    ) -> tuple[str, str, str, str | None]:
        """Resolve a scenario's target into (canonical_id, canonical_type, provider, namespace).

        Raises:
            UnresolvedTargetError if the target cannot be found in the Current Twin.
        """
        canonical_type, provider, slug = normalize_resource_type(resource_type_str)

        # 1. If resource_name is already a valid canonical ID and exists
        if resource_name in self.resources_by_id:
            res = self.resources_by_id[resource_name]
            resolved_ns = res.get("properties", {}).get("namespace", namespace)
            return res["id"], res["type"], res["provider"], resolved_ns

        # 2. Kubernetes lookup: (type, namespace, name)
        if provider == "kubernetes":
            ns = namespace or "default"
            key = (canonical_type, ns, resource_name)
            if key in self.k8s_index:
                return self.k8s_index[key], canonical_type, provider, ns

            # Search by name across any namespace if default wasn't found
            for (k_type, k_ns, k_name), r_id in self.k8s_index.items():
                if k_type == canonical_type and k_name == resource_name:
                    return r_id, canonical_type, provider, k_ns

            raise UnresolvedTargetError(
                f"Cannot resolve Kubernetes resource '{resource_name}' (type: {canonical_type}, namespace: {ns}) in Current Twin."
            )

        # 3. AWS lookup: by name or native identifier
        if provider == "aws":
            name_key = (canonical_type, resource_name)
            if name_key in self.aws_name_index:
                return self.aws_name_index[name_key], canonical_type, provider, None

            if resource_name in self.native_id_index:
                r_id = self.native_id_index[resource_name]
                res = self.resources_by_id[r_id]
                return r_id, res["type"], res["provider"], None

            raise UnresolvedTargetError(
                f"Cannot resolve AWS resource '{resource_name}' (type: {canonical_type}) in Current Twin."
            )

        raise UnresolvedTargetError(f"Target '{resource_name}' ({resource_type_str}) could not be resolved.")

    def resolve_terraform_target(
        self,
        tf_type: str,
        tf_name: str,
        before_state: dict[str, Any] | None,
        after_state: dict[str, Any] | None,
        is_create: bool = False,
    ) -> tuple[str, str, str]:
        """Resolve a Terraform plan resource to (canonical_id, canonical_type, provider)."""
        canonical_type, provider, slug = normalize_resource_type(tf_type)

        # 1. If not create, attempt to match using existing before state
        if not is_create and before_state:
            # Check direct id / arn
            id_val = before_state.get("id")
            if id_val:
                direct_id = f"{provider}:{slug}:{id_val}"
                if direct_id in self.resources_by_id:
                    return direct_id, canonical_type, provider
                if id_val in self.native_id_index:
                    return self.native_id_index[id_val], canonical_type, provider

            arn_val = before_state.get("arn")
            if arn_val:
                if arn_val in self.native_id_index:
                    return self.native_id_index[arn_val], canonical_type, provider
                direct_arn_id = f"{provider}:{slug}:{arn_val}"
                if direct_arn_id in self.resources_by_id:
                    return direct_arn_id, canonical_type, provider

            # Check tags.Name
            tags = before_state.get("tags") or {}
            if isinstance(tags, dict) and "Name" in tags:
                name_val = tags["Name"]
                if (canonical_type, name_val) in self.aws_name_index:
                    return self.aws_name_index[(canonical_type, name_val)], canonical_type, provider

            # Check tf_name
            if (canonical_type, tf_name) in self.aws_name_index:
                return self.aws_name_index[(canonical_type, tf_name)], canonical_type, provider

        # 2. For create (or newly planned resource): synthesize a canonical ID
        if is_create or not before_state:
            target_name = tf_name
            if after_state:
                tags = after_state.get("tags") or {}
                if isinstance(tags, dict) and "Name" in tags:
                    target_name = tags["Name"]
                elif "name" in after_state and after_state["name"]:
                    target_name = after_state["name"]

            # Format canonical ID: provider:slug:name
            canonical_id = f"{provider}:{slug}:{target_name}"
            return canonical_id, canonical_type, provider

        # If it wasn't create and couldn't be resolved, raise explicit error
        raise UnresolvedTargetError(
            f"Cannot resolve existing Terraform target {tf_type}.{tf_name} against Current Twin."
        )

    def resolve_kubernetes_manifest_target(
        self,
        kind: str,
        name: str,
        namespace: str = "default",
        allow_synthesize: bool = True,
    ) -> tuple[str, str, str, str]:
        """Resolve a Kubernetes manifest resource to (canonical_id, canonical_type, provider, namespace)."""
        canonical_type, provider, slug = normalize_resource_type(kind)
        ns = namespace or "default"

        key = (canonical_type, ns, name)
        if key in self.k8s_index:
            return self.k8s_index[key], canonical_type, provider, ns

        # If allowed (e.g. for creating new resources or manifests of new services)
        if allow_synthesize:
            canonical_id = (
                f"k8s:{slug}:{name}"
                if canonical_type in ("NAMESPACE", "NODE")
                else f"k8s:{slug}:{ns}:{name}"
            )
            return canonical_id, canonical_type, provider, ns

        raise UnresolvedTargetError(
            f"Cannot resolve Kubernetes manifest target {kind}/{name} in namespace '{ns}' against Current Twin."
        )
