"""Tests de RULE-NET-002 (Security Group ouvert à Internet) et RULE-NET-004 (Service public).

Les contextes sont construits sur le vrai jumeau du PoC (fixtures/poc/current_state.json)
ou sur les scénarios S03 / S04 d'Omar, passés par l'ingestion de Hamdi.
"""

import json
from pathlib import Path

import pytest

from analysis.engine import run
from analysis.models import SCHEMA_VERSION, AnalysisContext, Change, Severity, Status, Twin
from analysis.rules.security._network import parse_ingress
from analysis.rules.security.net_002_public_security_group import PublicSecurityGroupRule
from analysis.rules.security.net_004_public_service import PublicServiceRule
from ingestion import ChangeIngester
from integration.hamdi_fatma import context_from_changeset

HERE = Path(__file__).parent
POC_ROOT = HERE.parents[1]
REAL_TWIN = json.loads((HERE / "fixtures/poc/current_state.json").read_text(encoding="utf-8"))

SG = "aws:security-group:sg-0node"
SVC = "k8s:service:default:ui"
LB = ("aws:load-balancer:arn:aws:elasticloadbalancing:eu-west-3:123456789012:"
      "loadbalancer/net/ui-nlb/abc123")


def ctx(*changes: dict, twin: dict = REAL_TWIN) -> AnalysisContext:
    return AnalysisContext(
        schema_version=SCHEMA_VERSION, simulation_id="sim-test",
        current_twin=Twin.model_validate(twin),
        changes=[Change.model_validate({"change_id": f"change-{i:03d}", **c})
                 for i, c in enumerate(changes, 1)],
    )


def sg_change(before, after, path="properties.ingress", resource_id=SG, operation="update"):
    return {"operation": operation, "resource_id": resource_id, "resource_type": "SECURITY_GROUP",
            "provider": "aws", "property_path": path, "before": before, "after": after}


def svc_change(before, after, path="properties.service_type_and_scheme", resource_id=SVC,
               operation="update"):
    return {"operation": operation, "resource_id": resource_id, "resource_type": "SERVICE",
            "provider": "kubernetes", "property_path": path, "before": before, "after": after}


def tf_rule(port, cidrs, protocol="tcp", to_port=None):
    return {"protocol": protocol, "from_port": port, "to_port": to_port or port, "cidr_blocks": cidrs}


VPC_ONLY = [tf_rule(443, ["10.0.0.0/16"])]


def scenario_context(scenario_id: str, source: str):
    directory = next(POC_ROOT.joinpath("scenarios").glob(f"{scenario_id}-*"))
    twin = json.loads(directory.joinpath("current-twin.json").read_text(encoding="utf-8"))
    changeset = ChangeIngester(twin).ingest(directory / source)
    return context_from_changeset(changeset, twin)


# ----------------------------------------------------------- lecture de l'ingress

class TestParseIngress:
    def test_terraform_format(self):
        [e] = parse_ingress([tf_rule(22, ["0.0.0.0/0"])])
        assert (e.protocol, e.from_port, e.to_port, e.cidr) == ("tcp", 22, 22, "0.0.0.0/0")

    def test_aws_api_format_with_ipv6(self):
        rules = [{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
                  "IpRanges": [{"CidrIp": "10.0.0.0/16"}], "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]
        assert [e.cidr for e in parse_ingress(rules)] == ["10.0.0.0/16", "::/0"]

    def test_all_protocols_means_all_ports(self):
        [e] = parse_ingress([{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        assert e.protocol == "all" and e.covers(5432)

    def test_unreadable_rule_returns_none(self):
        assert parse_ingress([{"from_port": 22}]) is None
        assert parse_ingress(None) is None


# ----------------------------------------------------------------- RULE-NET-002

class TestNet002:
    rule = PublicSecurityGroupRule()

    def test_opening_ssh_to_internet_fails_high(self):
        [f] = self.rule.evaluate(ctx(sg_change(VPC_ONLY, VPC_ONLY + [tf_rule(22, ["0.0.0.0/0"])])))
        assert f.status == Status.FAIL and f.severity == Severity.HIGH
        assert f.evidence["newly_exposed"] == [
            {"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr": "0.0.0.0/0"}]
        assert f.evidence["overlaps_with"] == ["RULE-NET-001"]
        # Sur le vrai jumeau, le SG des nœuds protège l'EC2 et le cluster EKS.
        assert f.affected_resource_ids == sorted([SG, "aws:ec2:i-0node1", "aws:eks:retail-store"])
        assert "T1190" in f.reference and f.remediation

    def test_ipv6_any_is_public(self):
        [f] = self.rule.evaluate(ctx(sg_change(VPC_ONLY, VPC_ONLY + [
            {"protocol": "tcp", "from_port": 8080, "to_port": 8080, "ipv6_cidr_blocks": ["::/0"]}])))
        assert f.status == Status.FAIL

    def test_all_traffic_overlaps_ssh_and_database(self):
        after = [{"protocol": "-1", "from_port": 0, "to_port": 0, "cidr_blocks": ["0.0.0.0/0"]}]
        [f] = self.rule.evaluate(ctx(sg_change(VPC_ONLY, after)))
        assert f.status == Status.FAIL
        assert f.evidence["overlaps_with"] == ["RULE-NET-001", "RULE-NET-003"]

    def test_private_cidr_only_passes(self):
        [f] = self.rule.evaluate(ctx(sg_change(VPC_ONLY, VPC_ONLY + [tf_rule(22, ["10.0.0.0/8"])])))
        assert f.status == Status.PASS and f.remediation is None

    def test_existing_exposure_is_still_reported(self):
        public = [tf_rule(8080, ["0.0.0.0/0"])]
        [f] = self.rule.evaluate(ctx(sg_change(public, public + [tf_rule(443, ["10.0.0.0/16"])])))
        assert f.status == Status.FAIL and f.severity == Severity.MEDIUM
        assert f.evidence["newly_exposed"] == [] and len(f.evidence["already_exposed_before"]) == 1

    def test_https_on_public_load_balancer_sg_is_accepted(self):
        twin = json.loads(json.dumps(REAL_TWIN))
        twin["relationships"].append({"source": LB, "target": SG, "type": "USES"})
        [f] = self.rule.evaluate(ctx(sg_change([], [tf_rule(443, ["0.0.0.0/0"])]), twin=twin))
        assert f.status == Status.PASS and "accepted_reason" in f.evidence

    def test_https_on_internal_sg_still_fails(self):
        [f] = self.rule.evaluate(ctx(sg_change([], [tf_rule(443, ["0.0.0.0/0"])])))
        assert f.status == Status.FAIL

    def test_unknown_after_is_not_evaluated(self):
        # Valeur encore inconnue dans le plan (after_unknown) : on ne conclut pas.
        [f] = self.rule.evaluate(ctx(sg_change(VPC_ONLY, None)))
        assert f.status == Status.NOT_EVALUATED

    def test_create_with_full_resource(self):
        after = {"name": "new-sg", "ingress": [tf_rule(3389, ["0.0.0.0/0"])]}
        [f] = self.rule.evaluate(ctx(sg_change(None, after, path="properties", operation="create",
                                               resource_id="aws:security-group:sg-new")))
        assert f.status == Status.FAIL

    def test_other_property_is_ignored(self):
        assert self.rule.evaluate(ctx(sg_change("a", "b", path="properties.description"))) == []


# ----------------------------------------------------------------- RULE-NET-004

INTERNAL = {"service_type": "ClusterIP", "scheme": "internal"}
PUBLIC = {"service_type": "LoadBalancer", "scheme": "internet-facing"}
INTERNAL_TWIN = {
    "resources": [
        {"id": SVC, "name": "ui", "type": "SERVICE", "provider": "kubernetes",
         "properties": {"namespace": "default", "service_type": "ClusterIP", "annotations": {}}},
        {"id": "k8s:deployment:default:ui", "name": "ui", "type": "DEPLOYMENT",
         "provider": "kubernetes", "properties": {"replicas": 3}},
    ],
    "relationships": [{"source": SVC, "target": "k8s:deployment:default:ui", "type": "EXPOSES"}],
}


class TestNet004:
    rule = PublicServiceRule()

    def test_clusterip_to_internet_facing_fails_high(self):
        [f] = self.rule.evaluate(ctx(svc_change(INTERNAL, PUBLIC), twin=INTERNAL_TWIN))
        assert f.status == Status.FAIL and f.severity == Severity.HIGH
        assert f.affected_resource_ids == sorted([SVC, "k8s:deployment:default:ui"])
        assert f.evidence["before"]["exposure"] == "private"
        assert f.evidence["after"]["exposure"] == "public"

    def test_internal_load_balancer_passes(self):
        after = {"service_type": "LoadBalancer", "scheme": "internal"}
        [f] = self.rule.evaluate(ctx(svc_change(INTERNAL, after), twin=INTERNAL_TWIN))
        assert f.status == Status.PASS

    def test_source_ranges_restrict_exposure(self):
        after = {"type": "LoadBalancer", "loadBalancerSourceRanges": ["203.0.113.0/24"],
                 "annotations": {"service.beta.kubernetes.io/aws-load-balancer-scheme": "internet-facing"}}
        [f] = self.rule.evaluate(ctx(svc_change(INTERNAL, after, path="properties.spec"),
                                     twin=INTERNAL_TWIN))
        assert f.status == Status.PASS

    def test_scheme_read_from_annotation(self):
        ann = {"service.beta.kubernetes.io/aws-load-balancer-scheme": "internet-facing"}
        twin = json.loads(json.dumps(INTERNAL_TWIN))
        twin["resources"][0]["properties"]["service_type"] = "LoadBalancer"
        [f] = self.rule.evaluate(ctx(svc_change({}, ann, path="properties.annotations"), twin=twin))
        assert f.status == Status.FAIL

    def test_load_balancer_without_scheme_is_not_evaluated(self):
        [f] = self.rule.evaluate(ctx(svc_change(INTERNAL, {"service_type": "LoadBalancer"}),
                                     twin=INTERNAL_TWIN))
        assert f.status == Status.NOT_EVALUATED

    def test_nodeport_is_not_evaluated(self):
        [f] = self.rule.evaluate(ctx(svc_change(INTERNAL, {"service_type": "NodePort"}),
                                     twin=INTERNAL_TWIN))
        assert f.status == Status.NOT_EVALUATED

    def test_twin_proves_existing_public_service(self):
        # Vrai jumeau : un NLB internet-facing route déjà vers le Service ui.
        [f] = self.rule.evaluate(ctx(svc_change({}, {"team": "web"}, path="properties.annotations")))
        assert f.status == Status.FAIL and f.severity == Severity.MEDIUM
        assert LB in f.affected_resource_ids and f.evidence["public_load_balancer_in_twin"]

    def test_back_to_clusterip_passes(self):
        [f] = self.rule.evaluate(ctx(svc_change(PUBLIC, INTERNAL), twin=INTERNAL_TWIN))
        assert f.status == Status.PASS

    def test_other_property_is_ignored(self):
        assert self.rule.evaluate(ctx(svc_change(80, 8080, path="properties.ports"))) == []


# ---------------------------------------------- bout en bout : scénarios d'Omar

def _fails(result, rule_id):
    return [f for f in result.findings if f.rule_id == rule_id and f.status == Status.FAIL]


@pytest.mark.parametrize("source", ["scenario.json", "proposed-plan.json"])
def test_s04_produces_net_002(source):
    result = run(scenario_context("S04", source))
    [f] = _fails(result, "RULE-NET-002")
    assert f.severity == Severity.HIGH
    assert "k8s:statefulset:default:orders-postgresql" in f.affected_resource_ids
    assert f.evidence["overlaps_with"] == ["RULE-NET-003"]


def test_s03_scenario_produces_net_004():
    [f] = _fails(run(scenario_context("S03", "scenario.json")), "RULE-NET-004")
    assert f.severity == Severity.HIGH
    assert "k8s:deployment:default:ui" in f.affected_resource_ids


def test_s03_manifest_keeps_annotations_and_fails_like_the_scenario():
    # L'ingestion garde le schéma internet-facing des annotations : le manifest YAML donne
    # le même résultat que scenario.json (un seul changement service_type_and_scheme).
    result = run(scenario_context("S03", "proposed-service.yaml"))
    [f] = [f for f in result.findings if f.rule_id == "RULE-NET-004"]
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
