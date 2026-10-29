"""Moteur d'analyse : exécute toutes les règles sur un contexte et agrège les findings."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from analysis.models import SCHEMA_VERSION, AnalysisContext, AnalysisResult, Finding
from analysis.rules import ALL_RULES
from analysis.rules.base import Rule


class SchemaVersionError(ValueError):
    pass


def load_context(path: str | Path) -> AnalysisContext:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ctx = AnalysisContext.model_validate(data)
    if ctx.schema_version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"schema_version {ctx.schema_version!r} non supportée (attendu {SCHEMA_VERSION!r})"
        )
    return ctx


def _sort_key(f: Finding) -> tuple:
    # Ordre stable : mêmes entrées => même JSON, octet pour octet.
    return (f.rule_id, f.change_ids, f.status.value, f.affected_resource_ids)


def run(ctx: AnalysisContext, rules: list[Rule] | None = None) -> AnalysisResult:
    findings: list[Finding] = []
    for rule in rules if rules is not None else ALL_RULES:
        findings.extend(rule.evaluate(ctx))
    findings.sort(key=_sort_key)

    by_status = Counter(f.status.value for f in findings)
    by_severity = Counter(f.severity.value for f in findings if f.status.value == "fail")
    summary = {
        "total": len(findings),
        "fail": by_status.get("fail", 0),
        "pass": by_status.get("pass", 0),
        "not_evaluated": by_status.get("not_evaluated", 0),
        **{f"fail_{s}": by_severity.get(s, 0) for s in ("critical", "high", "medium", "low")},
    }
    return AnalysisResult(simulation_id=ctx.simulation_id, findings=findings, summary=summary)
