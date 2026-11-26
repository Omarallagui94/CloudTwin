"""L'IA propose des correctifs ; le jumeau les vérifie.

Le LLM reçoit les changements en échec (ressource, propriété, avant, après) et
propose une nouvelle valeur `after` pour certains d'entre eux. Chaque proposition
est d'abord filtrée (changement connu et en échec, même type de valeur), puis
rejouée dans le jumeau par analysis.remediation : elle n'est « vérifiée » que si
le score baisse sans nouveau risque. L'IA propose, les règles vérifient,
l'humain choisit.
"""

from __future__ import annotations

import json
from typing import Any

from ai_analyst.client import LLMClient
from analysis.models import AnalysisContext, AnalysisResult
from analysis.remediation import Fix, RemediationReport, remediate

SYSTEM = """Tu es l'analyste de CloudTwin AI. Tu reçois des changements d'infrastructure qui créent des risques.
Pour chacun que tu sais corriger, propose une NOUVELLE valeur "after" plus sûre, du même type que l'actuelle.
Ne change que des change_id présents dans la liste. Garde l'intention du changement quand c'est possible
(par exemple restreindre une source au lieu de tout fermer).
Réponds uniquement avec un objet JSON :
{"fixes": [{"change_id": "change-001", "after": <nouvelle valeur>, "description": "<une phrase>"}]}"""


def _failing_changes(ctx: AnalysisContext, result: AnalysisResult) -> list[dict[str, Any]]:
    risky: dict[str, list[str]] = {}
    for f in result.findings:
        if f.status == "fail":
            for cid in f.change_ids:
                risky.setdefault(cid, []).append(f.rule_id)
    return [
        {"change_id": c.change_id, "resource_type": c.resource_type, "property_path": c.property_path,
         "before": c.before, "after": c.after, "risks": sorted(set(risky[c.change_id]))}
        for c in ctx.changes if c.change_id in risky
    ]


def _same_shape(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    return type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float)))


def propose_fixes(ctx: AnalysisContext, result: AnalysisResult,
                  client: LLMClient) -> tuple[list[Fix], list[str]]:
    """(correctifs proposés par le LLM, raisons des rejets avant rejeu)."""
    failing = _failing_changes(ctx, result)
    if not failing:
        return [], []
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"changes": failing}, ensure_ascii=False)}]
    try:
        data = json.loads(client.complete(messages))
    except Exception as exc:          # LLM absent, lent ou réponse illisible : on garde le catalogue
        return [], [f"propositions de l'IA indisponibles : {type(exc).__name__}"]

    by_id = {c["change_id"]: c for c in failing}
    fixes, rejected = [], []
    for item in data.get("fixes", []) if isinstance(data, dict) else []:
        cid = item.get("change_id") if isinstance(item, dict) else None
        if cid not in by_id:
            rejected.append(f"{cid} : changement inconnu ou sans risque")
            continue
        if "after" not in item or not _same_shape(item["after"], by_id[cid]["after"]):
            rejected.append(f"{cid} : valeur absente ou d'un autre type que l'originale")
            continue
        description = str(item.get("description") or "Correctif proposé par l'IA")[:300]
        fixes.append(Fix(change_id=cid, rule_id=by_id[cid]["risks"][0], description=description,
                         after=item["after"], origin="llm"))
    return fixes, rejected


def remediate_with_ai(ctx: AnalysisContext, result: AnalysisResult,
                      client: LLMClient | None) -> tuple[RemediationReport, list[str]]:
    """Catalogue + propositions de l'IA, toutes rejouées dans le jumeau."""
    extra, rejected = propose_fixes(ctx, result, client) if client else ([], [])
    return remediate(ctx, result, extra=extra), rejected
