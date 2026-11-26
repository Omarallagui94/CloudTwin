"""Explication de secours, sans IA : construite uniquement à partir du rapport.

Elle sert quand aucun LLM n'est configuré, quand il ne répond pas, ou quand sa
réponse est rejetée par les garde-fous. Le rapport sort donc toujours.
"""

from __future__ import annotations

from typing import Any

from ai_analyst.schema import Explanation, Recommendation

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def recommendations(report: dict[str, Any]) -> list[Recommendation]:
    seen: set[str] = set()
    out: list[Recommendation] = []
    for risk in sorted(report["risks"], key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), r["rule_id"])):
        if risk["rule_id"] in seen or not risk.get("remediation"):
            continue
        seen.add(risk["rule_id"])
        out.append(Recommendation(rule_id=risk["rule_id"], action=risk["remediation"]))
    return out


def template_explanation(report: dict[str, Any], warnings: list[str] | None = None) -> Explanation:
    score, label = report["score"], report["label"]
    risks = report["risks"]

    if not risks:
        summary = f"Aucun risque détecté par les règles : {score}/100 ({label})."
    else:
        titles = sorted({r["title"] for r in risks})
        summary = (f"Ce changement obtient {score}/100 ({label}). "
                   f"Risques détectés : {', '.join(titles)}.")
        if any(r["rule_id"] == "RULE-IAM-003" for r in risks):
            summary += (" Pris ensemble, les changements ouvrent un chemin depuis Internet "
                        "jusqu'à un rôle IAM dangereux.")
    radius = report["blast_radius"]
    if radius["direct"] or radius["indirect"]:
        summary += (f" Il peut toucher {radius['direct']} ressource(s) directement "
                    f"et {radius['indirect']} indirectement.")
    if report["partial"]:
        summary += (f" Score minimum : {len(report['not_evaluated'])} règle(s) n'ont pas pu conclure "
                    f"({', '.join(report['not_evaluated'])}).")

    return Explanation(summary=summary, recommendations=recommendations(report),
                       source="template", warnings=warnings or [])
