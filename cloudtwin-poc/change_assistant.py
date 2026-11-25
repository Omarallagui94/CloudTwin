"""Fail-closed natural-language parser for the CloudTwin demo UI.

The parser only translates a small set of explicit, deterministic phrases into
the existing scenario ingestion shape. It does not call an LLM, discover
resources, infer dependencies, or mutate infrastructure.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


class UnsupportedChangeRequest(ValueError):
    """Raised when a request cannot be translated with high confidence."""


@dataclass(frozen=True)
class ParsedChangeRequest:
    """A proposed change ready for Hamdi's existing scenario ingester."""

    scenario: dict[str, Any]
    summary: str


def _custom_id(text: str) -> str:
    digest = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"CUSTOM-{digest.upper()}"


def parse_change_request(text: str) -> ParsedChangeRequest:
    """Parse a supported request or fail without guessing missing information."""
    request = " ".join(text.strip().split())
    if not request:
        raise UnsupportedChangeRequest("The change request is empty.")

    scale = re.fullmatch(
        r"(?:scale|set|change|increase|reduce)\s+(?:the\s+)?"
        r"(?P<name>[a-z0-9][a-z0-9-]*)"
        r"(?:\s+deployment)?(?:\s+replicas?)?\s+from\s+"
        r"(?P<before>\d+)\s+to\s+(?P<after>\d+)(?:\s+replicas?)?",
        request,
        flags=re.IGNORECASE,
    )
    if scale:
        name = scale.group("name").lower()
        before = int(scale.group("before"))
        after = int(scale.group("after"))
        if before == after:
            raise UnsupportedChangeRequest("The current and proposed replica counts are identical.")
        scenario = {
            "scenario_id": _custom_id(request),
            "name": f"Scale {name} deployment",
            "description": request,
            "target_resource": f"k8s:deployment:default:{name}",
            "resource_type": "DEPLOYMENT",
            "operation": "update",
            "property": "replicas",
            "before": before,
            "after": after,
        }
        return ParsedChangeRequest(scenario, f"Scale {name} from {before} to {after} replicas")

    exposure = re.fullmatch(
        r"(?:make|expose)\s+(?:the\s+)?(?P<name>[a-z0-9][a-z0-9-]*)"
        r"\s+service\s+(?:public|publicly)",
        request,
        flags=re.IGNORECASE,
    )
    if exposure:
        name = exposure.group("name").lower()
        scenario = {
            "scenario_id": _custom_id(request),
            "name": f"Expose {name} service publicly",
            "description": request,
            "target_resource": f"k8s:service:default:{name}",
            "resource_type": "SERVICE",
            "operation": "update",
            "property": "service_type_and_scheme",
            "before": {"service_type": "ClusterIP", "scheme": "internal"},
            "after": {"service_type": "LoadBalancer", "scheme": "internet-facing"},
        }
        return ParsedChangeRequest(scenario, f"Expose the {name} service publicly")

    security_group = re.fullmatch(
        r"open\s+(?:the\s+)?security\s+group\s+(?P<name>[a-z0-9-]+)\s+"
        r"port\s+(?P<port>\d{1,5})\s+to\s+(?P<cidr>\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})",
        request,
        flags=re.IGNORECASE,
    )
    if security_group:
        name = security_group.group("name").lower()
        port = int(security_group.group("port"))
        cidr = security_group.group("cidr")
        if not 1 <= port <= 65535:
            raise UnsupportedChangeRequest("The requested port is outside the valid TCP range.")
        scenario = {
            "scenario_id": _custom_id(request),
            "name": f"Open security group {name}",
            "description": request,
            "target_resource": f"aws:security-group:{name}",
            "resource_type": "SECURITY_GROUP",
            "operation": "update",
            "property": "ingress_rules",
            "before": [],
            "after": [{
                "protocol": "tcp",
                "from_port": port,
                "to_port": port,
                "cidr_blocks": [cidr],
            }],
        }
        return ParsedChangeRequest(scenario, f"Allow TCP {port} from {cidr} on {name}")

    dependency = re.fullmatch(
        r"remove\s+(?:the\s+)?orders[- ]postgresql\s+dependency",
        request,
        flags=re.IGNORECASE,
    )
    if dependency:
        scenario = {
            "scenario_id": _custom_id(request),
            "name": "Remove orders PostgreSQL dependency",
            "description": request,
            "target_resource": "k8s:service:default:orders-postgresql",
            "resource_type": "SERVICE",
            "operation": "delete",
            "property": "resource",
            "before": "present",
            "after": "absent",
        }
        return ParsedChangeRequest(scenario, "Remove the orders PostgreSQL Service dependency")

    raise UnsupportedChangeRequest(
        "I could not confidently translate this request into a CloudTwin change."
    )
