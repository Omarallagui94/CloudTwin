"""Tests de RULE-NET-001 (SSH / RDP ouverts à Internet), sur le vrai jumeau du PoC."""

import pytest

from analysis.engine import run
from analysis.models import Severity, Status
from analysis.rules.security.net_001_public_admin_port import PublicAdminPortRule
from analysis.tests.test_network_rules import SG, VPC_ONLY, ctx, scenario_context, sg_change, tf_rule

rule = PublicAdminPortRule()


def test_ssh_open_to_internet_fails_high():
    [f] = rule.evaluate(ctx(sg_change(VPC_ONLY, VPC_ONLY + [tf_rule(22, ["0.0.0.0/0"])])))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
    assert f.evidence["newly_exposed"] == [{"port": 22, "service": "SSH", "cidr": "0.0.0.0/0"}]
    assert f.affected_resource_ids == sorted([SG, "aws:ec2:i-0node1", "aws:eks:retail-store"])
    assert "5.3" in f.reference and f.remediation


def test_rdp_over_ipv6_fails():
    rdp = {"protocol": "tcp", "from_port": 3389, "to_port": 3389, "ipv6_cidr_blocks": ["::/0"]}
    [f] = rule.evaluate(ctx(sg_change(VPC_ONLY, VPC_ONLY + [rdp])))
    assert f.status == Status.FAIL and f.evidence["newly_exposed"][0]["service"] == "RDP"


def test_wide_port_range_covers_ssh():
    [f] = rule.evaluate(ctx(sg_change([], [tf_rule(0, ["0.0.0.0/0"], to_port=1024)])))
    assert f.status == Status.FAIL


def test_all_traffic_covers_both_ports():
    after = [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    [f] = rule.evaluate(ctx(sg_change([], after, path="properties.ingress_rules")))
    assert {e["port"] for e in f.evidence["newly_exposed"]} == {22, 3389}


@pytest.mark.parametrize("after", [
    [tf_rule(22, ["10.0.0.0/16"])],                  # SSH limité au VPC
    [tf_rule(443, ["0.0.0.0/0"])],                   # public, mais pas un port d'admin
    [tf_rule(22, ["0.0.0.0/0"], protocol="udp")],    # UDP/22 : pas SSH
])
def test_safe_ingress_passes(after):
    [f] = rule.evaluate(ctx(sg_change([], after)))
    assert f.status == Status.PASS and f.remediation is None


def test_existing_ssh_exposure_is_still_reported():
    ssh = [tf_rule(22, ["0.0.0.0/0"])]
    [f] = rule.evaluate(ctx(sg_change(ssh, ssh + [tf_rule(443, ["10.0.0.0/16"])])))
    assert f.status == Status.FAIL and f.severity == Severity.MEDIUM


def test_closing_ssh_passes():
    [f] = rule.evaluate(ctx(sg_change([tf_rule(22, ["0.0.0.0/0"])], VPC_ONLY)))
    assert f.status == Status.PASS


def test_unknown_after_is_not_evaluated():
    [f] = rule.evaluate(ctx(sg_change(VPC_ONLY, None)))
    assert f.status == Status.NOT_EVALUATED


def test_s04_database_port_does_not_trigger_net_001():
    result = run(scenario_context("S04", "proposed-plan.json"))
    [f] = [f for f in result.findings if f.rule_id == "RULE-NET-001"]
    assert f.status == Status.PASS
