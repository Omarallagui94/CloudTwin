"""Tests de RULE-RES-002 (Multi-AZ RDS).

Le jumeau reste synthétique afin d'isoler la règle; la découverte RDS et le
type RDS_INSTANCE sont testés séparément dans les tests du collecteur.
"""

from analysis.engine import run
from analysis.models import SCHEMA_VERSION, AnalysisContext, Change, Severity, Status, Twin
from analysis.rules.resilience.res_002_rds_multi_az import RdsMultiAzRule

RDS = "aws:rds:orders-db"
APP = "k8s:deployment:default:orders"
TWIN = {
    "resources": [
        {"id": RDS, "name": "orders-db", "type": "RDS_INSTANCE", "provider": "aws",
         "properties": {"engine": "postgres", "multi_az": True}},
        {"id": APP, "name": "orders", "type": "DEPLOYMENT", "provider": "kubernetes",
         "properties": {"replicas": 3}},
    ],
    "relationships": [{"source": APP, "target": RDS, "type": "USES"}],
}


def ctx(before, after, path="properties.multi_az", operation="update", twin=TWIN, rid=RDS):
    return AnalysisContext(
        schema_version=SCHEMA_VERSION, simulation_id="sim-test",
        current_twin=Twin.model_validate(twin),
        changes=[Change(change_id="change-001", operation=operation, resource_id=rid,
                        resource_type="RDS_INSTANCE", provider="aws", property_path=path,
                        before=before, after=after)],
    )


rule = RdsMultiAzRule()


def test_true_to_false_fails_high():
    [f] = rule.evaluate(ctx(True, False))
    assert f.status == Status.FAIL and f.severity == Severity.HIGH
    assert f.affected_resource_ids == sorted([RDS, APP])
    assert "RDS.5" in f.reference and f.remediation


def test_aws_api_property_name():
    [f] = rule.evaluate(ctx(True, False, path="properties.MultiAZ"))
    assert f.status == Status.FAIL


def test_enabling_multi_az_passes():
    [f] = rule.evaluate(ctx(False, True))
    assert f.status == Status.PASS and f.remediation is None


def test_already_single_az_is_not_a_new_loss():
    [f] = rule.evaluate(ctx(False, False))
    assert f.status == Status.PASS


def test_create_without_multi_az_fails_medium():
    after = {"identifier": "new-db", "engine": "mysql", "multi_az": False}
    [f] = rule.evaluate(ctx(None, after, path="properties", operation="create",
                            rid="aws:rds:new-db"))
    assert f.status == Status.FAIL and f.severity == Severity.MEDIUM


def test_unknown_after_is_not_evaluated():
    [f] = rule.evaluate(ctx(True, None))
    assert f.status == Status.NOT_EVALUATED


def test_non_boolean_is_not_evaluated():
    [f] = rule.evaluate(ctx(True, "false"))
    assert f.status == Status.NOT_EVALUATED


def test_cluster_member_is_skipped():
    after = {"multi_az": False, "cluster_identifier": "aurora-main"}
    assert rule.evaluate(ctx({"multi_az": True}, after, path="properties",
                             operation="replace")) == []


def test_other_property_is_ignored():
    assert rule.evaluate(ctx("db.t3.micro", "db.t3.small", path="properties.instance_class")) == []


def test_registered_in_engine():
    result = run(ctx(True, False))
    assert [f.rule_id for f in result.findings if f.status == Status.FAIL] == ["RULE-RES-002"]
