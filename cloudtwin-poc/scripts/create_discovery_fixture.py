
"""Create a reconstructed offline discovery fixture from a saved snapshot.

This is a regression fixture reconstructed from normalized resource properties.
It is not a raw capture from live AWS or Kubernetes APIs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fixtures" / "sample_current_state_baseline.json"
OUTPUT = ROOT / "fixtures" / "sample_discovery_input.json"

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


def reconstruct_fixture(snapshot: dict[str, Any]) -> dict[str, Any]:
    aws = {key: [] for key in AWS_COLLECTIONS.values()}
    kubernetes = {
        key: [] for key in KUBERNETES_COLLECTIONS.values()
    }

    for resource in snapshot.get("resources", []):
        resource_type = resource["type"]
        properties = resource.get("properties", {})

        if resource_type in AWS_COLLECTIONS:
            aws[AWS_COLLECTIONS[resource_type]].append(properties)

        elif resource_type in KUBERNETES_COLLECTIONS:
            kubernetes[
                KUBERNETES_COLLECTIONS[resource_type]
            ].append(properties)

        elif resource_type == "INTERNET":
            # The normalizer derives this resource from public load balancers.
            continue

        else:
            raise ValueError(
                f"Unsupported resource type: {resource_type}"
            )

    # The normalized snapshot combines warning messages from both collectors,
    # so this reconstructed fixture does not claim to preserve their origin.
    aws["warnings"] = []
    kubernetes["warnings"] = []

    metadata = snapshot.get("metadata", {})

    return {
        "fixture_type": "reconstructed_from_normalized_snapshot",
        "source": "sample_current_state_baseline.json",
        "metadata": {
            "region": metadata.get("region"),
            "cluster": metadata.get("cluster"),
        },
        "aws": aws,
        "kubernetes": kubernetes,
        "baseline": {
            "resources": len(snapshot.get("resources", [])),
            "relationships": len(snapshot.get("relationships", [])),
            "aws_resources": snapshot.get("summary", {}).get(
                "aws_resources"
            ),
            "kubernetes_resources": snapshot.get("summary", {}).get(
                "kubernetes_resources"
            ),
        },
    }


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(
            f"Baseline snapshot not found: {SOURCE}"
        )

    snapshot = json.loads(SOURCE.read_text(encoding="utf-8"))
    fixture = reconstruct_fixture(snapshot)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(fixture, indent=2),
        encoding="utf-8",
    )

    print(f"Fixture created: {OUTPUT}")
    print(f"Fixture type: {fixture['fixture_type']}")
    print(f"AWS input collections: {len(fixture['aws'])}")
    print(
        "Kubernetes input collections: "
        f"{len(fixture['kubernetes'])}"
    )
    print(f"Baseline resources: {fixture['baseline']['resources']}")
    print(
        "Baseline relationships: "
        f"{fixture['baseline']['relationships']}"
    )


if __name__ == "__main__":
    main()

