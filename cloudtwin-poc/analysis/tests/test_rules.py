"""Tests des règles. Chaque règle a au moins : un cas positif, un négatif, un cas de données manquantes.

Les fixtures sont générées avec le VRAI normalizer de cloudtwin-poc : mêmes IDs,
mêmes types (STATEFUL_SET, LOAD_BALANCER...) et mêmes 8 types de relations.
"""

from pathlib import Path

import pytest

from analysis.engine import load_context
from analysis.models import Relationship, Severity, Status
from analysis.rules.resilience.res_001_single_replica import SingleReplicaRule
from analysis.rules.resilience.res_003_deleted_dependency import DEPENDENCY_DIRECTION, DeletedDependencyRule
from analysis.rules.security.net_003_public_database_port import PublicDatabasePortRule

FIX = Path(__file__).parent / "fixtures"
LB = ("aws:load-balancer:arn:aws:elasticloadbalancing:eu-west-3:123456789012:"
      "loadbalancer/net/ui-nlb/abc123")


def evaluate(rule, fixture):
    return rule.evaluate(load_context(FIX / fixture))


# ------------------------------------------------------------- RULE-RES-001

class TestRes001:
    rule = SingleReplicaRule()

    def test_3_to_1_fails_high(self):
        [f] = evaluate(self.rule, "res001/positive_3_to_1.json")
        assert f.status == Status.FAIL
        assert f.severity == Severity.HIGH
        assert f.change_ids == ["change-001"]
        assert f.evidence["before"] == 3 and f.evidence["after"] == 1
        assert "k8s:service:default:ui" in f.affected_resource_ids
        assert f.remediation

    def test_3_to_0_is_critical(self):
        [f] = evaluate(self.rule, "res001/positive_3_to_0_critical.json")
        assert f.status == Status.FAIL
        assert f.severity == Severity.CRITICAL

    def test_statefulset_uses_real_type_name(self):
        # Le normalizer produit "STATEFUL_SET" (avec underscore)
        [f] = evaluate(self.rule, "res001/positive_statefulset_2_to_1.json")
        assert f.status == Status.FAIL

    def test_1_to_3_passes(self):
        [f] = evaluate(self.rule, "res001/negative_1_to_3.json")
        assert f.status == Status.PASS
        assert f.remediation is None

    def test_other_property_is_ignored(self):
        assert evaluate(self.rule, "res001/negative_unrelated_property.json") == []

    def test_missing_value_is_not_evaluated(self):
        [f] = evaluate(self.rule, "res001/missing_after_value.json")
        assert f.status == Status.NOT_EVALUATED  # jamais un faux « pass »


# ------------------------------------------------------------- RULE-RES-003

class TestRes003:
    rule = DeletedDependencyRule()

    def test_deleting_exposed_deployment_fails(self):
        # Le Service catalog EXPOSES le Deployment catalog
        [f] = evaluate(self.rule, "res003/positive_delete_exposed_deployment.json")
        assert f.status == Status.FAIL
        assert f.severity == Severity.MEDIUM
        assert f.evidence["dependents"] == ["k8s:service:default:catalog"]

    def test_deleting_routed_service_fails(self):
        # Le Load Balancer ET l'Ingress ROUTES_TO le Service ui
        [f] = evaluate(self.rule, "res003/positive_delete_routed_service.json")
        assert f.status == Status.FAIL
        assert f.evidence["dependents"] == [LB, "k8s:ingress:default:ui"]

    def test_deleting_unused_ingress_passes(self):
        [f] = evaluate(self.rule, "res003/negative_delete_unused_ingress.json")
        assert f.status == Status.PASS

    def test_dependents_deleted_together_pass(self):
        findings = evaluate(self.rule, "res003/negative_delete_with_dependents.json")
        assert {f.status for f in findings} == {Status.PASS}
        svc = next(f for f in findings if f.change_ids == ["change-001"])
        assert svc.evidence["dependents_also_deleted"] == [LB, "k8s:ingress:default:ui"]

    def test_unknown_resource_is_not_evaluated(self):
        [f] = evaluate(self.rule, "res003/missing_unknown_resource.json")
        assert f.status == Status.NOT_EVALUATED

    def test_unknown_relationship_type_is_not_a_pass(self):
        ctx = load_context(FIX / "res003/negative_delete_unused_ingress.json")
        ctx.current_twin.relationships.append(Relationship(
            source="k8s:deployment:default:ui", target="k8s:ingress:default:ui", type="MOUNTS",
        ))
        [f] = self.rule.evaluate(ctx)
        assert f.status == Status.NOT_EVALUATED
        assert f.evidence["unknown_relationship_types"] == ["MOUNTS"]

    def test_all_real_relationship_types_are_known(self):
        ctx = load_context(FIX / "res003/negative_delete_unused_ingress.json")
        real_types = {r.type for r in ctx.current_twin.relationships}
        assert real_types <= set(DEPENDENCY_DIRECTION), real_types - set(DEPENDENCY_DIRECTION)


# ------------------------------------------------------------- RULE-NET-003

SG = "aws:security-group:sg-0node"


class TestNet003:
    rule = PublicDatabasePortRule()

    def test_terraform_5432_opened_to_internet_is_critical(self):
        # Format exact produit par l'ingestion Terraform de Hamdi
        [f] = evaluate(self.rule, "net003/positive_terraform_5432_public.json")
        assert f.status == Status.FAIL
        assert f.severity == Severity.CRITICAL
        assert f.change_ids == ["change-001"]
        assert f.evidence["newly_exposed"] == [
            {"port": 5432, "engine": "PostgreSQL / Aurora PostgreSQL", "cidr": "0.0.0.0/0"}
        ]
        assert f.evidence["already_exposed_before"] == []
        assert f.reference == "Exposition potentielle associée à MITRE ATT&CK T1190"
        assert f.remediation

    def test_users_of_the_security_group_are_affected(self):
        # Le nœud EC2 et le cluster EKS USES sg-0node dans le jumeau réel
        [f] = evaluate(self.rule, "net003/positive_terraform_5432_public.json")
        assert f.affected_resource_ids == ["aws:ec2:i-0node1", "aws:eks:retail-store", SG]

    def test_aws_api_format_and_ipv6_are_read(self):
        # Format IpPermissions de la découverte d'Omar, ouverture en ::/0
        [f] = evaluate(self.rule, "net003/positive_aws_api_3306_ipv6.json")
        assert f.status == Status.FAIL
        assert [e["port"] for e in f.evidence["exposures"]] == [3306]
        assert f.evidence["exposures"][0]["cidr"] == "::/0"

    def test_all_traffic_rule_exposes_every_database_port(self):
        [f] = evaluate(self.rule, "net003/positive_all_traffic_public.json")
        assert f.status == Status.FAIL
        assert [e["port"] for e in f.evidence["exposures"]] == [3306, 5432]

    def test_created_group_with_wide_port_range_fails(self):
        [f] = evaluate(self.rule, "net003/positive_create_wide_port_range.json")
        assert f.status == Status.FAIL
        assert [e["port"] for e in f.evidence["newly_exposed"]] == [3306, 5432]

    def test_preexisting_exposure_is_still_reported_but_not_as_new(self):
        ctx = load_context(FIX / "net003/positive_terraform_5432_public.json")
        ctx.changes[0].before = ctx.changes[0].after
        [f] = self.rule.evaluate(ctx)
        assert f.status == Status.FAIL
        assert f.evidence["newly_exposed"] == []
        assert len(f.evidence["already_exposed_before"]) == 1
        assert "laisse ouvert" in f.description

    def test_vpc_only_access_passes(self):
        [f] = evaluate(self.rule, "net003/negative_vpc_only.json")
        assert f.status == Status.PASS
        assert f.remediation is None

    def test_public_https_is_not_a_database_exposure(self):
        # 443 public : c'est le rôle de NET-002, pas de NET-003
        [f] = evaluate(self.rule, "net003/negative_public_https_only.json")
        assert f.status == Status.PASS

    def test_other_property_is_ignored(self):
        assert evaluate(self.rule, "net003/negative_unrelated_property.json") == []

    def test_unknown_future_rules_are_not_evaluated(self):
        # after = null : cas `after_unknown` de terraform plan
        [f] = evaluate(self.rule, "net003/missing_after_unknown.json")
        assert f.status == Status.NOT_EVALUATED

    def test_missing_port_range_is_not_a_pass(self):
        [f] = evaluate(self.rule, "net003/missing_port_range.json")
        assert f.status == Status.NOT_EVALUATED
        assert f.evidence["unreadable_rule_indexes"] == [1]

    def test_unreadable_rule_does_not_hide_a_clear_exposure(self):
        ctx = load_context(FIX / "net003/missing_port_range.json")
        ctx.changes[0].after.append(
            {"protocol": "tcp", "from_port": 3306, "to_port": 3306, "cidr_blocks": ["0.0.0.0/0"]}
        )
        [f] = self.rule.evaluate(ctx)
        assert f.status == Status.FAIL

    def test_udp_on_database_port_is_ignored(self):
        ctx = load_context(FIX / "net003/negative_vpc_only.json")
        ctx.changes[0].after = [
            {"protocol": "udp", "from_port": 5432, "to_port": 5432, "cidr_blocks": ["0.0.0.0/0"]}
        ]
        [f] = self.rule.evaluate(ctx)
        assert f.status == Status.PASS


@pytest.mark.parametrize("rule", [SingleReplicaRule(), DeletedDependencyRule(), PublicDatabasePortRule()])
def test_rule_metadata_is_complete(rule):
    assert rule.rule_id.startswith("RULE-")
    assert rule.version and rule.title and rule.remediation
