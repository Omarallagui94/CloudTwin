"""Tests du blast radius, sur le vrai jumeau du PoC."""

from analysis.blast_radius import compute
from analysis.engine import run
from analysis.models import AffectedResource
from analysis.tests.test_network_rules import LB, SG, VPC_ONLY, ctx, sg_change

UI_DEPLOY = "k8s:deployment:default:ui"
UI_SVC = "k8s:service:default:ui"


def replicas(resource_id=UI_DEPLOY, change_from=3, change_to=1):
    return {"operation": "update", "resource_id": resource_id, "resource_type": "DEPLOYMENT",
            "provider": "kubernetes", "property_path": "properties.replicas",
            "before": change_from, "after": change_to}


def test_deployment_hits_its_service_then_the_load_balancer():
    r = compute(ctx(replicas()))
    assert r.source == "computed"
    assert r.direct == [UI_SVC]                                    # le Service dépend du Deployment
    assert r.indirect == sorted([LB, "k8s:ingress:default:ui"])    # LB et Ingress dépendent du Service
    lb = next(a for a in r.affected if a.target == LB)
    assert lb.distance == 2 and lb.path == [UI_DEPLOY, UI_SVC, LB] and lb.relationship == "ROUTES_TO"


def test_security_group_hits_ec2_and_eks_directly():
    r = compute(ctx(sg_change(VPC_ONLY, VPC_ONLY)))
    assert r.direct == ["aws:ec2:i-0node1", "aws:eks:retail-store"]
    assert "k8s:node:ip-10-0-2-10" in r.indirect           # le nœud tourne sur l'EC2
    assert "k8s:pod:default:ui-7d9f-abcde" in r.indirect   # le Pod tourne sur le nœud
    assert SG not in r.direct + r.indirect


def test_depth_is_limited():
    r = compute(ctx(sg_change(VPC_ONLY, VPC_ONLY)), max_depth=1)
    assert r.indirect == [] and len(r.direct) == 2
    assert max(a.distance for a in compute(ctx(sg_change(VPC_ONLY, VPC_ONLY))).affected) == 3


def test_internet_is_never_impacted():
    # Internet EXPOSES le LB : supprimer le LB ne « casse » pas Internet.
    change = {"operation": "delete", "resource_id": LB, "resource_type": "LOAD_BALANCER",
              "provider": "aws", "property_path": "properties"}
    assert "aws:internet" not in compute(ctx(change)).direct


def test_changed_resources_are_not_listed_as_impacted():
    r = compute(ctx(replicas(), {**replicas(), "resource_id": UI_SVC, "resource_type": "SERVICE",
                                 "property_path": "properties.annotations", "before": {}, "after": {}}))
    assert UI_SVC not in r.direct + r.indirect


def test_direct_wins_over_indirect_across_changes():
    # LB : indirect depuis le Deployment, mais direct depuis le Service modifié.
    svc = {"operation": "update", "resource_id": UI_SVC, "resource_type": "SERVICE",
           "provider": "kubernetes", "property_path": "properties.annotations", "before": {}, "after": {}}
    r = compute(ctx(svc))
    assert LB in r.direct


def test_provided_affected_resources_are_used_as_is():
    c = ctx(replicas())
    given = [AffectedResource(change_id="change-001", source=UI_DEPLOY, target="x", relationship="USES",
                              direction="incoming", distance=2, impact="indirect", path=[UI_DEPLOY, "y", "x"])]
    r = compute(c.model_copy(update={"affected_resources": given}))
    assert r.source == "provided" and r.indirect == ["x"]


def test_engine_reports_radius_and_adds_the_score_bonus():
    result = run(ctx(replicas()))
    assert result.blast_radius["direct"] == [UI_SVC]
    # RES-001 HIGH (20) + 2 ressources indirectes (+2)
    assert result.risk["blast_radius_bonus"] == 2 and result.risk["score"] == 22
    # RES-001 liste maintenant le Service touché, calculé par le blast radius.
    [f] = [f for f in result.findings if f.rule_id == "RULE-RES-001"]
    assert UI_SVC in f.affected_resource_ids


def test_same_input_same_radius():
    assert compute(ctx(sg_change(VPC_ONLY, VPC_ONLY))) == compute(ctx(sg_change(VPC_ONLY, VPC_ONLY)))
