"""Garde-fous : on vérifie la réponse du LLM au lieu de lui faire confiance."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ai_analyst.schema import Explanation

# « T1190 détecté », « technique T1098.003 detected »… : une exposition n'est pas une attaque.
DETECTED = re.compile(r"\bT\d{4}(?:\.\d{3})?\b[^.]{0,20}\b(détect\w*|detected|exploit[ée]\w*)", re.I)
SCORE_OUT_OF_100 = re.compile(r"\b(\d{1,3})\s*/\s*100\b")
SCORE_WORD = re.compile(r"\bscore\b[^.\d]{0,20}(\d{1,3})\b", re.I)


def parse(raw: str) -> tuple[Explanation | None, list[str]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, ["la réponse n'est pas du JSON valide"]
    try:
        return Explanation.model_validate({**data, "source": "llm"}), []
    except ValidationError as exc:
        return None, [f"format invalide : {exc.errors()[0]['msg']}"]


def check(explanation: Explanation, report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    failing = {r["rule_id"] for r in report["risks"]}

    invented = sorted({r.rule_id for r in explanation.recommendations} - failing)
    if invented:
        errors.append(f"rule_id absents du rapport : {', '.join(invented)}")
    cited = set(re.findall(r"RULE-[A-Z0-9]+-\d{3}", explanation.summary)) - failing
    if cited:
        errors.append(f"le résumé cite des règles absentes : {', '.join(sorted(cited))}")

    text = " ".join([explanation.summary] + [r.action for r in explanation.recommendations])
    scores = {int(m) for m in SCORE_OUT_OF_100.findall(text)} | {int(m) for m in SCORE_WORD.findall(text)}
    wrong = sorted(s for s in scores if s != report["score"])
    if wrong:
        errors.append(f"score modifié : {wrong} au lieu de {report['score']}")
    if DETECTED.search(text):
        errors.append("une technique MITRE est présentée comme « détectée » : c'est une exposition potentielle")
    return errors
