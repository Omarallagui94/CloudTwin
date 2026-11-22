"""Tests des correctifs proposés par l'IA et vérifiés par le jumeau (LLM simulé)."""

from ai_analyst.fixes import propose_fixes, remediate_with_ai
from ai_analyst.tests.test_analyst import FakeLLM
from analysis.engine import run
from analysis.tests.test_scoring import VPC_8080, demo_changes

CTX = demo_changes("security_group")
RESULT = run(CTX)


def llm_options(report):
    return [o for o in report.options if o.origin == "llm"]


def test_a_good_ai_fix_is_verified():
    restricted = [{**VPC_8080, "cidr_blocks": ["10.0.0.0/16"]}]
    llm = FakeLLM({"fixes": [{"change_id": "change-003", "after": restricted,
                              "description": "Limiter le port 8080 au VPC"}]})
    report, rejected = remediate_with_ai(CTX, RESULT, llm)
    [option] = llm_options(report)
    assert option.verified and option.score_after == 60 and option.proposed_after == restricted
    assert rejected == []
    # L'IA ne voit que les changements en échec, pas le jumeau.
    sent = llm.calls[0][-1]["content"]
    assert "change-003" in sent and "relationships" not in sent


def test_an_ai_fix_that_does_not_help_is_shown_as_rejected_by_the_twin():
    still_open = [{**VPC_8080, "cidr_blocks": ["0.0.0.0/0", "10.0.0.0/16"]}]
    llm = FakeLLM({"fixes": [{"change_id": "change-003", "after": still_open, "description": "Ajouter le VPC"}]})
    report, _ = remediate_with_ai(CTX, RESULT, llm)
    [option] = llm_options(report)
    assert not option.verified and option.score_after == 80


def test_invalid_proposals_are_filtered_before_replay():
    llm = FakeLLM({"fixes": [
        {"change_id": "change-099", "after": 2},                      # changement inconnu
        {"change_id": "change-001", "after": "deux"},                 # mauvais type (int attendu)
        {"change_id": "change-002", "after": 1},                      # bool attendu, pas un entier
    ]})
    fixes, rejected = propose_fixes(CTX, RESULT, llm)
    assert fixes == [] and len(rejected) == 3


def test_llm_down_keeps_the_catalog():
    report, rejected = remediate_with_ai(CTX, RESULT, FakeLLM(TimeoutError("timed out")))
    assert len(report.options) == 4 and llm_options(report) == []
    assert "indisponibles" in rejected[0]


def test_without_llm_only_the_catalog_is_used():
    report, rejected = remediate_with_ai(CTX, RESULT, None)
    assert len(report.options) == 4 and rejected == []
