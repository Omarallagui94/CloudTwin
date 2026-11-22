"""Rejouer un correctif dans le jumeau et mesurer son effet.

Le correctif est appliqué au contexte (la valeur `after` du changement est
remplacée, ou le changement est annulé), puis tout le moteur est relancé :
blast radius, règles, score. Un correctif est **vérifié** seulement s'il fait
baisser le score sans créer de nouveau risque.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from analysis.engine import run
from analysis.models import AnalysisContext, AnalysisResult
from analysis.remediation.catalog import Fix, propose


class FixOutcome(BaseModel):
    change_ids: list[str]
    rule_ids: list[str]
    description: str
    origin: str                     # "catalog" ou "llm"
    score_before: int
    score_after: int
    level_after: str
    resolved: list[str]             # règles qui ne sont plus en échec
    new_risks: list[str]            # règles en échec apparues avec le correctif
    verified: bool
    proposed_after: Any = None      # la nouvelle valeur, pour l'appliquer au vrai code


class RemediationReport(BaseModel):
    score_before: int
    options: list[FixOutcome]       # un correctif à la fois, du plus efficace au moins efficace
    all_fixes: FixOutcome | None    # tous les correctifs vérifiés ensemble


def apply(ctx: AnalysisContext, fixes: list[Fix]) -> AnalysisContext:
    by_change = {f.change_id: f for f in fixes}
    changes = []
    for change in ctx.changes:
        fix = by_change.get(change.change_id)
        if fix is None:
            changes.append(change)
        elif not fix.reverts:
            changes.append(change.model_copy(update={"after": fix.after}))
    return ctx.model_copy(update={"changes": changes})


def _failing(result: AnalysisResult) -> set[tuple[str, tuple[str, ...]]]:
    return {(f.rule_id, tuple(f.change_ids)) for f in result.findings if f.status == "fail"}


def evaluate(ctx: AnalysisContext, baseline: AnalysisResult, fixes: list[Fix],
             description: str | None = None) -> FixOutcome:
    after = run(apply(ctx, fixes))
    before_fail, after_fail = _failing(baseline), _failing(after)
    before_rules = {r for r, _ in before_fail}
    resolved = sorted({r for r, _ in before_fail} - {r for r, _ in after_fail})
    new_risks = sorted({r for r, _ in after_fail} - before_rules)
    score_before, score_after = baseline.risk["score"], after.risk["score"]
    single = fixes[0] if len(fixes) == 1 else None
    return FixOutcome(
        change_ids=sorted({f.change_id for f in fixes}),
        rule_ids=sorted({f.rule_id for f in fixes}),
        description=description or (single.description if single else "Tous les correctifs"),
        origin=single.origin if single else "catalog",
        score_before=score_before, score_after=score_after, level_after=after.risk["label"],
        resolved=resolved, new_risks=new_risks,
        verified=score_after < score_before and not new_risks,
        proposed_after=None if single is None or single.reverts else single.after,
    )


def remediate(ctx: AnalysisContext, baseline: AnalysisResult | None = None,
              extra: list[Fix] | None = None) -> RemediationReport:
    """Propose, rejoue et classe les correctifs. `extra` = propositions de l'IA."""
    baseline = baseline or run(ctx)
    candidates = propose(ctx, baseline.findings) + list(extra or [])
    pairs = [(evaluate(ctx, baseline, [fix]), fix) for fix in candidates]
    pairs.sort(key=lambda p: (p[0].score_after, not p[0].verified, p[0].change_ids, p[0].description))
    options = [outcome for outcome, _ in pairs]

    # Ensemble : le meilleur correctif vérifié pour chaque changement.
    best: dict[str, Fix] = {}
    for outcome, fix in pairs:
        if outcome.verified and fix.change_id not in best:
            best[fix.change_id] = fix
    combined = evaluate(ctx, baseline, list(best.values()), "Tous les correctifs vérifiés") if best else None
    return RemediationReport(score_before=baseline.risk["score"], options=options, all_fixes=combined)


def summary(report: RemediationReport) -> str:
    lines = [f"Score actuel : {report.score_before}/100"]
    for o in report.options:
        mark = "✔" if o.verified else "✘"
        lines.append(f"{mark} {o.description} → {o.score_after}/100 ({o.score_after - o.score_before:+d})")
    if report.all_fixes:
        lines.append(f"Tous les correctifs vérifiés → {report.all_fixes.score_after}/100 "
                     f"({report.all_fixes.level_after})")
    return "\n".join(lines)
