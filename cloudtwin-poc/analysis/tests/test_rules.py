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


@pytest.mark.parametrize("rule", [SingleReplicaRule(), DeletedDependencyRule()])
def test_rule_metadata_is_complete(rule):
    assert rule.rule_id.startswith("RULE-")
    assert rule.version and rule.title and rule.remediation
