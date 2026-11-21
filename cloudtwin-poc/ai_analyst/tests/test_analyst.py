"""Tests de l'analyste IA, avec un LLM simulé (aucun appel réseau)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_analyst import explain
from ai_analyst.client import OllamaClient, from_env
from analysis.engine import run
from analysis.tests.test_scoring import demo_changes

POC_ROOT = Path(__file__).resolve().parents[2]
DEMO = run(demo_changes("security_group"))          # 80/100, 4 règles en échec
FAILING = ["RULE-IAM-002", "RULE-NET-002", "RULE-RES-001", "RULE-RES-002"]


class FakeLLM:
    model = "fake"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, messages):
        self.calls.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def good_answer(**override):
    answer = {
        "summary": ("Ce changement obtient 80/100 (Critique). L'API perd sa redondance au moment où "
                    "son Security Group s'ouvre à Internet et où son rôle reçoit iam:PassRole."),
        "recommendations": [{"rule_id": r, "action": "Revenir à la configuration précédente."} for r in FAILING],
    }
    return {**answer, **override}


# ---------------------------------------------------------------- sans LLM

def test_without_llm_the_template_is_used():
    e = explain(DEMO, client=None)
    assert e.source == "template"
    assert "80/100 (Critique)" in e.summary
    assert sorted(r.rule_id for r in e.recommendations) == FAILING


def test_template_mentions_the_combination():
    from analysis.tests.test_scoring import demo_changes as demo
    e = explain(run(demo("service")), client=None)
    assert "chemin depuis Internet" in e.summary


def test_template_for_a_safe_change():
    from analysis.tests.test_network_rules import VPC_ONLY, ctx, sg_change
    e = explain(run(ctx(sg_change(VPC_ONLY, VPC_ONLY))), client=None)
    assert e.summary.startswith("Aucun risque détecté") and e.recommendations == []


# --------------------------------------------------------------- avec LLM

def test_valid_llm_answer_is_used():
    llm = FakeLLM(good_answer())
    e = explain(DEMO, client=llm, cache={})
    assert e.source == "llm" and e.model == "fake" and len(llm.calls) == 1
    # Le LLM ne voit que le rapport réduit : pas le jumeau.
    sent = llm.calls[0][-1]["content"]
    assert '"score": 80' in sent and "relationships" not in sent


def test_invented_rule_is_rejected_then_retried():
    bad = good_answer(recommendations=[{"rule_id": "RULE-NET-009", "action": "Fermer le port."}])
    llm = FakeLLM(bad, good_answer())
    e = explain(DEMO, client=llm, cache={})
    assert e.source == "llm" and len(llm.calls) == 2
    assert "RULE-NET-009" in llm.calls[1][-1]["content"]      # la raison du refus est renvoyée


@pytest.mark.parametrize("summary, reason", [
    ("Ce changement obtient 95/100 : très dangereux pour la production.", "score modifié"),
    ("Le score passe à 60 à cause du Security Group ouvert sur Internet.", "score modifié"),
    ("Le port ouvert : T1190 détecté sur l'API, attaque en cours.", "exposition potentielle"),
    ("Voir RULE-K8S-001 pour le conteneur privilégié de l'API.", "règles absentes"),
])
def test_guards_fall_back_to_the_template(summary, reason):
    llm = FakeLLM(good_answer(summary=summary), good_answer(summary=summary))
    e = explain(DEMO, client=llm, cache={})
    assert e.source == "template" and len(llm.calls) == 2
    assert any(reason in w for w in e.warnings)


def test_non_json_answer_falls_back():
    e = explain(DEMO, client=FakeLLM("Voici mon analyse : tout va bien.", "toujours pas du JSON"), cache={})
    assert e.source == "template" and "JSON" in e.warnings[0]


def test_markdown_fenced_json_is_accepted():
    e = explain(DEMO, client=FakeLLM("```json\n" + json.dumps(good_answer()) + "\n```"), cache={})
    assert e.source == "llm"


def test_llm_down_never_blocks_the_report():
    e = explain(DEMO, client=FakeLLM(TimeoutError("timed out")), cache={})
    assert e.source == "template" and "LLM indisponible" in e.warnings[0]


def test_forgotten_rules_get_the_template_recommendation():
    partial = good_answer(recommendations=[{"rule_id": "RULE-NET-002", "action": "Restreindre au VPC."}])
    e = explain(DEMO, client=FakeLLM(partial), cache={})
    assert sorted(r.rule_id for r in e.recommendations) == FAILING
    assert e.recommendations[0].action == "Restreindre au VPC."


def test_cache_avoids_a_second_call():
    llm, cache = FakeLLM(good_answer()), {}
    first = explain(DEMO, client=llm, cache=cache)
    second = explain(DEMO, client=llm, cache=cache)
    assert first == second and len(llm.calls) == 1


# ------------------------------------------------------------ configuration

def test_client_from_env(monkeypatch):
    monkeypatch.delenv("CLOUDTWIN_LLM", raising=False)
    assert from_env() is None
    monkeypatch.setenv("CLOUDTWIN_LLM", "ollama")
    monkeypatch.setenv("CLOUDTWIN_LLM_MODEL", "mistral")
    client = from_env()
    assert isinstance(client, OllamaClient) and client.model == "mistral"


def test_cli_end_to_end(tmp_path):
    report = tmp_path / "rapport.json"
    report.write_text(DEMO.model_dump_json(), encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "ai_analyst", str(report)], cwd=POC_ROOT,
                         capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "CLOUDTWIN_LLM"})
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["source"] == "template"


def test_analysis_cli_adds_ai_explanation(tmp_path):
    ctx_file = tmp_path / "ctx.json"
    ctx_file.write_text(demo_changes("security_group").model_dump_json(), encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "analysis", str(ctx_file), "--explain"], cwd=POC_ROOT,
                         capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "CLOUDTWIN_LLM"})
    report = json.loads(out.stdout)
    assert report["risk"]["score"] == 80
    assert report["ai_explanation"]["source"] == "template"
    assert "80/100" in report["ai_explanation"]["summary"]
