"""RULE-NET-003 — Port de base de données ouvert à Internet.

Une règle d'ingress de Security Group qui autorise 0.0.0.0/0 (ou ::/0) sur un
port de base de données rend la base joignable depuis n'importe où : il ne
reste que l'authentification de la base entre elle et un attaquant.

Logique :
  - on regarde les changements d'ingress des SECURITY_GROUP ;
  - FAIL           si l'état futur autorise une source publique sur un port
                   de DATABASE_PORTS (protocole TCP ou « tous protocoles ») ;
  - PASS           si aucune règle future n'expose un port de base de données ;
  - NOT_EVALUATED  si l'état futur est inconnu (ex. `after_unknown` Terraform)
                   ou si une règle est illisible et qu'aucune autre ne suffit
                   à conclure FAIL.

Deux formats de règle sont acceptés, car le jumeau en reçoit deux :
  - Terraform (ingestion de Hamdi) : protocol, from_port, to_port,
    cidr_blocks, ipv6_cidr_blocks ;
  - API AWS (découverte d'Omar, `ingress_rules`) : IpProtocol, FromPort,
    ToPort, IpRanges[].CidrIp, Ipv6Ranges[].CidrIpv6.

Référence : exposition potentielle associée à MITRE ATT&CK T1190 (Exploit
Public-Facing Application, tactique Initial Access) — vérifié sur
attack.mitre.org. Une exposition n'est PAS une exploitation.
"""

from __future__ import annotations

from typing import Any

from analysis.models import AnalysisContext, Category, Change, Finding, Severity, Status
from analysis.rules.base import Rule

SECURITY_GROUP_TYPE = "SECURITY_GROUP"

# Chemins produits par l'ingestion : Terraform -> "properties.ingress",
# scénario d'Omar / découverte -> "properties.ingress_rules".
INGRESS_PATHS = {"properties.ingress", "properties.ingress_rules"}
WHOLE_RESOURCE_PATH = "properties"   # create / replace Terraform : état complet

# Catalogue MVP (docs : RULE-NET-003). Étendre ici si l'équipe le décide.
DATABASE_PORTS = {
    3306: "MySQL / MariaDB / Aurora MySQL",
    5432: "PostgreSQL / Aurora PostgreSQL",
}
PUBLIC_CIDRS = {"0.0.0.0/0", "::/0"}

TCP_PROTOCOLS = {"tcp", "6"}
ALL_PROTOCOLS = {"-1", "all"}


class _Unreadable(ValueError):
    """Une règle d'ingress dont on ne peut pas déduire les ports ou sources."""


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _cidrs(entry: dict[str, Any]) -> set[str]:
    cidrs: set[str] = set()
    for key in ("cidr_blocks", "ipv6_cidr_blocks"):
        values = entry.get(key) or []
        if not isinstance(values, list):
            raise _Unreadable(f"{key} n'est pas une liste")
        cidrs.update(str(v) for v in values)
    for key, field in (("IpRanges", "CidrIp"), ("Ipv6Ranges", "CidrIpv6")):
        values = entry.get(key) or []
        if not isinstance(values, list):
            raise _Unreadable(f"{key} n'est pas une liste")
        cidrs.update(str(v.get(field)) for v in values if isinstance(v, dict) and v.get(field))
    return cidrs


def _exposed_ports(entry: Any) -> set[tuple[int, str]]:
    """Retourne les couples (port BDD, CIDR public) autorisés par une règle."""
    if not isinstance(entry, dict):
        raise _Unreadable("règle d'ingress non structurée")

    public = _cidrs(entry) & PUBLIC_CIDRS
    if not public:
        return set()

    protocol = str(entry.get("protocol", entry.get("IpProtocol", ""))).lower()
    if protocol in ALL_PROTOCOLS:
        ports = set(DATABASE_PORTS)
    elif protocol in TCP_PROTOCOLS:
        low = _int_or_none(entry.get("from_port", entry.get("FromPort")))
        high = _int_or_none(entry.get("to_port", entry.get("ToPort")))
        if low is None or high is None:
            raise _Unreadable("plage de ports TCP absente")
        ports = {p for p in DATABASE_PORTS if low <= p <= high}
    elif protocol:
        return set()          # udp, icmp... : pas un port de base de données TCP
    else:
        raise _Unreadable("protocole absent")

    return {(port, cidr) for port in ports for cidr in public}


_MISSING = object()   # le côté existe mais ne contient pas de clé d'ingress


def _ingress_list(change: Change, side: str) -> Any:
    """Extrait la liste d'ingress d'un côté du changement (before / after)."""
    value = getattr(change, side)
    if change.property_path == WHOLE_RESOURCE_PATH:
        if value is None:
            return None
        if not isinstance(value, dict):
            return _MISSING
        for key in ("ingress", "ingress_rules"):
            if key in value:
                return value[key]
        return _MISSING
    return value


def _analyse(rules: Any) -> tuple[set[tuple[int, str]], list[int]]:
    """Retourne (expositions, index des règles illisibles)."""
    exposures: set[tuple[int, str]] = set()
    unreadable: list[int] = []
    for index, entry in enumerate(rules):
        try:
            exposures |= _exposed_ports(entry)
        except _Unreadable:
            unreadable.append(index)
    return exposures, unreadable


def _as_evidence(exposures: set[tuple[int, str]]) -> list[dict[str, Any]]:
    return [
        {"port": port, "engine": DATABASE_PORTS[port], "cidr": cidr}
        for port, cidr in sorted(exposures)
    ]


class PublicDatabasePortRule(Rule):
    rule_id = "RULE-NET-003"
    version = "1.0"
    category = Category.SECURITY
    severity = Severity.CRITICAL
    title = "Base de données ouverte à Internet"
    remediation = (
        "Retirer 0.0.0.0/0 et ::/0 des règles d'ingress sur les ports de base de "
        "données ; n'autoriser que le CIDR du VPC ou, mieux, le Security Group "
        "des applications clientes."
    )
    reference = "Exposition potentielle associée à MITRE ATT&CK T1190"

    def evaluate(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        for change in ctx.changes:
            if change.resource_type.upper() != SECURITY_GROUP_TYPE:
                continue
            if change.operation not in ("update", "create", "replace"):
                continue
            if change.property_path not in INGRESS_PATHS | {WHOLE_RESOURCE_PATH}:
                continue

            after = _ingress_list(change, "after")
            before = _ingress_list(change, "before")
            affected = [change.resource_id] + self._users(ctx, change)
            evidence: dict[str, Any] = {
                "property_path": change.property_path,
                "database_ports": sorted(DATABASE_PORTS),
            }

            if after is _MISSING or not isinstance(after, list):
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"Règles d'ingress futures inconnues pour {change.resource_id}.",
                    change_ids=[change.change_id],
                    affected=[change.resource_id],
                    evidence=evidence,
                ))
                continue

            exposures, unreadable = _analyse(after)
            before_exposures, _ = _analyse(before) if isinstance(before, list) else (set(), [])
            evidence["unreadable_rule_indexes"] = unreadable

            if exposures:
                new = exposures - before_exposures
                evidence["exposures"] = _as_evidence(exposures)
                evidence["newly_exposed"] = _as_evidence(new)
                evidence["already_exposed_before"] = _as_evidence(exposures & before_exposures)
                ports = ", ".join(str(p) for p in sorted({p for p, _ in exposures}))
                origin = "ouvre" if new else "laisse ouvert"
                findings.append(self.finding(
                    Status.FAIL,
                    f"{change.resource_id} {origin} le(s) port(s) {ports} à Internet.",
                    change_ids=[change.change_id],
                    affected=affected,
                    evidence=evidence,
                ))
            elif unreadable:
                findings.append(self.finding(
                    Status.NOT_EVALUATED,
                    f"{len(unreadable)} règle(s) d'ingress illisible(s) sur {change.resource_id}.",
                    change_ids=[change.change_id],
                    affected=[change.resource_id],
                    evidence=evidence,
                ))
            else:
                findings.append(self.finding(
                    Status.PASS,
                    f"{change.resource_id} n'expose aucun port de base de données à Internet.",
                    change_ids=[change.change_id],
                    affected=[change.resource_id],
                    evidence=evidence,
                ))

        return findings

    @staticmethod
    def _users(ctx: AnalysisContext, change: Change) -> list[str]:
        """Ressources qui utilisent ce Security Group (relation USES du jumeau)
        et ressources directement impactées selon Hamdi."""
        users = [
            r.source for r in ctx.current_twin.relationships
            if r.target == change.resource_id and r.type == "USES"
        ]
        users += [
            a.target for a in ctx.affected_resources
            if a.change_id == change.change_id and a.distance == 1
        ]
        return users
