"""Outils communs aux règles réseau (RULE-NET-*).

Les règles d'ingress d'un Security Group arrivent sous deux formes :
  - Terraform (ingestion de Hamdi) : protocol, from_port, to_port,
    cidr_blocks, ipv6_cidr_blocks ;
  - API AWS (découverte d'Omar)   : IpProtocol, FromPort, ToPort,
    IpRanges[].CidrIp, Ipv6Ranges[].CidrIpv6.

Ce module les ramène à une seule forme : une liste d'entrées
(protocole, port_min, port_max, cidr). Si une règle est illisible, il
renvoie None : la règle appelante répond alors `not_evaluated`, jamais `pass`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from analysis.models import Change, Twin

PUBLIC_CIDRS = frozenset({"0.0.0.0/0", "::/0"})
ALL_PROTOCOLS = frozenset({"-1", "all"})

# Chemins de propriété qui portent les règles d'ingress d'un SG.
INGRESS_PATHS = {"properties.ingress", "properties.ingress_rules"}
INGRESS_KEYS = ("ingress", "ingress_rules", "IpPermissions")

# Ports « connus » : sert seulement à signaler le chevauchement avec d'autres règles.
OVERLAPS = {
    "RULE-NET-001": (22, 3389),
    "RULE-NET-003": (3306, 5432),
}


@dataclass(frozen=True, order=True)
class IngressEntry:
    protocol: str   # "tcp", "udp", "icmp" ou "all"
    from_port: int
    to_port: int
    cidr: str

    @property
    def is_public(self) -> bool:
        return self.cidr in PUBLIC_CIDRS

    def covers(self, port: int) -> bool:
        return self.protocol in ("tcp", "all") and self.from_port <= port <= self.to_port

    def to_dict(self) -> dict[str, Any]:
        return {"protocol": self.protocol, "from_port": self.from_port,
                "to_port": self.to_port, "cidr": self.cidr}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _parse_rule(rule: Any) -> list[IngressEntry] | None:
    if not isinstance(rule, dict):
        return None

    raw_proto = rule.get("protocol", rule.get("IpProtocol"))
    if raw_proto is None:
        return None
    proto = str(raw_proto).lower()
    if proto in ALL_PROTOCOLS:
        proto, lo, hi = "all", 0, 65535
    else:
        # Numéros IANA courants : 6 = tcp, 17 = udp.
        proto = {"6": "tcp", "17": "udp", "1": "icmp"}.get(proto, proto)
        lo = _int(rule.get("from_port", rule.get("FromPort")))
        hi = _int(rule.get("to_port", rule.get("ToPort")))
        if lo is None or hi is None:
            return None

    cidrs: list[Any] = []
    cidrs += rule.get("cidr_blocks") or []
    cidrs += rule.get("ipv6_cidr_blocks") or []
    cidrs += [r.get("CidrIp") for r in rule.get("IpRanges") or [] if isinstance(r, dict)]
    cidrs += [r.get("CidrIpv6") for r in rule.get("Ipv6Ranges") or [] if isinstance(r, dict)]
    if not all(isinstance(c, str) for c in cidrs):
        return None
    # Une règle sans CIDR (source = un autre SG ou une prefix list) n'ouvre rien à Internet.
    return [IngressEntry(proto, lo, hi, c) for c in cidrs]


def parse_ingress(rules: Any) -> list[IngressEntry] | None:
    """Liste d'entrées, ou None si le contenu est inconnu ou illisible."""
    if not isinstance(rules, list):
        return None
    entries: list[IngressEntry] = []
    for rule in rules:
        parsed = _parse_rule(rule)
        if parsed is None:
            return None
        entries.extend(parsed)
    return entries


def ingress_from_change(change: Change, side: str) -> tuple[bool, Any]:
    """Extrait les règles d'ingress du côté `before` ou `after` d'un changement.

    Retourne (concerné, valeur) : `concerné` vaut False si le changement ne
    touche pas l'ingress (la règle l'ignore alors complètement).
    """
    value = getattr(change, side)
    if change.property_path in INGRESS_PATHS:
        return True, value
    if change.property_path in (None, "properties") and change.operation in ("create", "replace"):
        if value is None:
            return True, [] if side == "before" else None
        if isinstance(value, dict):
            for key in INGRESS_KEYS:
                if key in value:
                    return True, value[key]
            return True, None
    return False, None


def neighbours(ctx_twin: Twin, resource_id: str, rel_types: set[str] | None = None) -> list[str]:
    """Voisins immédiats dans le jumeau (dans les deux sens)."""
    out = []
    for rel in ctx_twin.relationships:
        if rel_types and rel.type not in rel_types:
            continue
        if rel.target == resource_id:
            out.append(rel.source)
        elif rel.source == resource_id:
            out.append(rel.target)
    return out


def overlapping_rules(entries: list[IngressEntry]) -> list[str]:
    return sorted(
        rule_id for rule_id, ports in OVERLAPS.items()
        if any(e.covers(p) for e in entries for p in ports)
    )
