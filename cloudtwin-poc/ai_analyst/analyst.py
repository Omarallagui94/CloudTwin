"""Point d'entrée : explain(rapport) -> Explanation.

1. On réduit le rapport à ce que le LLM a le droit de voir (report.slim).
2. Cache : même rapport + même modèle = même explication, sans nouvel appel.
3. On appelle le LLM, puis on vérifie sa réponse (guards). En cas de refus,
   un seul nouvel essai, avec la raison du refus.
4. Toujours refusé, ou LLM absent / en panne : explication de secours (template).
5. Les règles en échec que le LLM a oubliées reçoivent la recommandation du template.
"""

from __future__ import annotations

from typing import Any

from ai_analyst import guards, prompt
from ai_analyst.client import LLMClient, from_env
from ai_analyst.report import fingerprint, slim
from ai_analyst.schema import Explanation
from ai_analyst.template import recommendations, template_explanation

MAX_ATTEMPTS = 2
_CACHE: dict[str, Explanation] = {}

_UNSET = object()


def explain(result: Any, client: LLMClient | None | object = _UNSET,
            cache: dict[str, Explanation] | None = None) -> Explanation:
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    report = slim(data)
    llm = from_env() if client is _UNSET else client
    if llm is None:
        return template_explanation(report)

    cache = _CACHE if cache is None else cache
    key = fingerprint(report, llm.model)
    if key in cache:
        return cache[key]

    warnings: list[str] = []
    feedback = None
    for _ in range(MAX_ATTEMPTS):
        try:
            raw = llm.complete(prompt.messages(report, feedback))
        except Exception as exc:     # réseau, délai, modèle absent : on ne bloque jamais le rapport
            warnings.append(f"LLM indisponible : {type(exc).__name__}")
            break
        explanation, errors = guards.parse(raw)
        if explanation is not None:
            errors = guards.check(explanation, report)
        if not errors:
            explanation = _complete(explanation, report, llm.model)
            cache[key] = explanation
            return explanation
        warnings.extend(errors)
        feedback = "; ".join(errors)

    return template_explanation(report, warnings=warnings)


def _complete(explanation: Explanation, report: dict[str, Any], model: str) -> Explanation:
    """Ajoute la recommandation du template pour chaque règle en échec oubliée par le LLM."""
    covered = {r.rule_id for r in explanation.recommendations}
    missing = [r for r in recommendations(report) if r.rule_id not in covered]
    return explanation.model_copy(update={
        "recommendations": explanation.recommendations + missing,
        "source": "llm", "model": model,
    })
