"""Score global de risque (0–100), déterministe et recalculable à la main.

Formule (présentation §6) :

    score = min(100, Σ poids(gravité) + bonus_blast_radius)
    poids  : CRITICAL = 30 · HIGH = 20 · MEDIUM = 10 · LOW = 5
    bonus  : +1 par ressource indirectement impactée, plafonné à 10
    niveau : 0–29 Faible · 30–59 Moyen · 60–79 Élevé · 80–100 Critique

Règles de comptage :
  1. Seuls les findings `fail` comptent.
  2. Un même risque n'est compté qu'une fois. Quand plusieurs règles
     décrivent la même ouverture sur le même changement (famille
     « ingress public » : NET-001, NET-002, NET-003), seul le finding le plus
     grave compte ; les autres apparaissent avec `counted = false`.
  3. RULE-IAM-003 compte en plus des règles locales : le chemin créé par la
     combinaison est un risque distinct de chacun de ses maillons.
  4. Si des règles ont répondu `not_evaluated`, le score est un **minimum**
     (`partial = true`) : le risque réel peut être plus élevé.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from analysis.models import AffectedResource, Finding, Severity, Status

WEIGHTS = {Severity.CRITICAL: 30, Severity.HIGH: 20, Severity.MEDIUM: 10, Severity.LOW: 5}
BLAST_RADIUS_CAP = 10
LEVELS = [  # (seuil minimal, niveau, libellé)
    (80, Severity.CRITICAL, "Critique"),
    (60, Severity.HIGH, "Élevé"),
    (30, Severity.MEDIUM, "Moyen"),
    (0, Severity.LOW, "Faible"),
]
# Règles qui décrivent le même risque quand elles touchent le même changement.
OVERLAP_FAMILIES = {
    "RULE-NET-001": "sg-public-ingress",
    "RULE-NET-002": "sg-public-ingress",
    "RULE-NET-003": "sg-public-ingress",
}
FORMULA = ("min(100, Σ poids + bonus) ; CRITICAL=30, HIGH=20, MEDIUM=10, LOW=5 ; "
           "bonus = +1 par ressource indirecte, max 10")


class ScoreLine(BaseModel):
    rule_id: str
    severity: Severity
    change_ids: list[str]
    weight: int
    counted: bool
    reason: str | None = None


class RiskScore(BaseModel):
    score: int
    level: Severity
    label: str
    partial: bool
    not_evaluated: int
    findings_points: int
    blast_radius_bonus: int
    indirect_resources: list[str]
    lines: list[ScoreLine]
    formula: str = FORMULA


def level_for(score: int) -> tuple[Severity, str]:
    for threshold, level, label in LEVELS:
        if score >= threshold:
            return level, label
    return Severity.LOW, "Faible"


def compute_score(findings: list[Finding],
                  affected_resources: list[AffectedResource] | None = None) -> RiskScore:
    fails = [f for f in findings if f.status == Status.FAIL]

    # Meilleur finding par groupe (famille de règles, changements concernés).
    best: dict[tuple, Finding] = {}
    for f in fails:
        key = (OVERLAP_FAMILIES.get(f.rule_id, f.rule_id), tuple(f.change_ids))
        current = best.get(key)
        if current is None or WEIGHTS[f.severity] > WEIGHTS[current.severity] or (
                WEIGHTS[f.severity] == WEIGHTS[current.severity] and f.rule_id < current.rule_id):
            best[key] = f

    counted_ids = {id(f) for f in best.values()}
    lines: list[ScoreLine] = []
    for f in fails:
        counted = id(f) in counted_ids
        winner = best[(OVERLAP_FAMILIES.get(f.rule_id, f.rule_id), tuple(f.change_ids))]
        lines.append(ScoreLine(
            rule_id=f.rule_id, severity=f.severity, change_ids=f.change_ids,
            weight=WEIGHTS[f.severity] if counted else 0, counted=counted,
            reason=None if counted else f"même risque que {winner.rule_id}, déjà compté"))

    indirect = sorted({a.target for a in affected_resources or [] if a.impact == "indirect"})
    bonus = min(BLAST_RADIUS_CAP, len(indirect))
    points = sum(line.weight for line in lines)
    score = min(100, points + bonus)
    level, label = level_for(score)
    not_evaluated = sum(1 for f in findings if f.status == Status.NOT_EVALUATED)

    return RiskScore(
        score=score, level=level, label=label,
        partial=not_evaluated > 0, not_evaluated=not_evaluated,
        findings_points=points, blast_radius_bonus=bonus, indirect_resources=indirect,
        lines=sorted(lines, key=lambda l: (-l.weight, l.rule_id, l.change_ids)),
    )


def explain(score: RiskScore) -> str:
    """Le calcul en une ligne, pour le rapport et pour le jury."""
    parts = [f"{l.rule_id} {l.severity.value.upper()} +{l.weight}" for l in score.lines if l.counted]
    if score.blast_radius_bonus:
        parts.append(f"blast radius +{score.blast_radius_bonus}")
    total = " + ".join(parts) or "aucun risque"
    capped = " (plafonné à 100)" if score.findings_points + score.blast_radius_bonus > 100 else ""
    suffix = f" — minimum : {score.not_evaluated} règle(s) non évaluée(s)" if score.partial else ""
    return f"{total} = {score.score}/100{capped} → {score.label}{suffix}"


def as_dict(score: RiskScore) -> dict[str, Any]:
    return score.model_dump(mode="json")
