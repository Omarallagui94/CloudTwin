"""CloudTwin Current Twin discovery entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from aws_collector import AWSCollector
from kubernetes_collector import KubernetesCollector
from normalizer import normalize

REGION = "eu-west-3"
CLUSTER = "retail-store"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "current_state.json"


def _status(label: str, count: int) -> None:
    print(f"[OK] {label}: {count}")


def main() -> int:
    print("CloudTwin Discovery\n")
    print(f"Connecting to AWS {REGION}...\n")
    try:
        aws = AWSCollector(REGION).collect()
    except Exception as exc:
        print(f"[WARNING] AWS connection failed: {exc}")
        aws = {"warnings": [str(exc)]}

    for key, label in [
        ("vpcs", "VPCs discovered"), ("subnets", "Subnets discovered"),
        ("security_groups", "Security Groups discovered"), ("instances", "EC2 instances discovered"),
        ("clusters", "EKS clusters discovered"), ("nodegroups", "EKS node groups discovered"),
        ("load_balancers", "Load Balancers discovered"), ("iam_roles", "IAM roles discovered"),
    ]:
        _status(label, len(aws.get(key, [])))
    for warning in aws.get("warnings", []):
        print(f"[WARNING] {warning}")

    print("\nConnecting to Kubernetes...\n")
    try:
        kubernetes = KubernetesCollector().collect()
    except Exception as exc:
        print(f"[WARNING] Kubernetes connection failed: {exc}")
        kubernetes = {"warnings": [str(exc)]}
    for key, label in [
        ("namespaces", "Namespaces discovered"), ("nodes", "Nodes discovered"),
        ("deployments", "Deployments discovered"), ("pods", "Pods discovered"),
        ("services", "Services discovered"), ("statefulsets", "StatefulSets discovered"),
        ("service_accounts", "ServiceAccounts discovered"), ("ingresses", "Ingresses discovered"),
        ("network_policies", "NetworkPolicies discovered"),
    ]:
        _status(label, len(kubernetes.get(key, [])))
    for warning in kubernetes.get("warnings", []):
        print(f"[WARNING] {warning}")

    print("\nNormalizing resources...\n")
    twin = normalize(aws, kubernetes, REGION, CLUSTER)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(twin, indent=2, sort_keys=False), encoding="utf-8")
    print(f"[OK] Current Twin generated: {OUTPUT}")
    print(f"\nAWS resources: {twin['summary']['aws_resources']}")
    print(f"Kubernetes resources: {twin['summary']['kubernetes_resources']}")
    print(f"Relationships: {twin['summary']['relationships']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
