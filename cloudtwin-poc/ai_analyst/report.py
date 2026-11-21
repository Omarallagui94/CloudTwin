"""Ce que le LLM a le droit de voir : un extrait du rapport d'analyse.

Jamais le jumeau, jamais d'ARN de compte complet, jamais de secret : seulement
les findings en échec, le score et le blast radius résumé.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def slim(result: dict[str, Any]) -> dict[str, Any]:
    findings = result.get("findings", [])
    risk = result.get("risk") or {}
    radius = result.get("blast_radius") or {}
    return {
        "score": risk.get("score", 0),
        "label": risk.get("label", "Faible"),
        "partial": bool(risk.get("partial")),
        "score_explanation": risk.get("explanation"),
        "risks": [
            {
                "rule_id": f["rule_id"],
                "severity": f["severity"],
                "title": f["title"],
                "description": f["description"],
                "remediation": f.get("remediation"),
                "reference": f.get("reference"),
            }
            for f in findings if f.get("status") == "fail"
        ],
        "not_evaluated": sorted({f["rule_id"] for f in findings if f.get("status") == "not_evaluated"}),
        "blast_radius": {
            "direct": len(radius.get("direct", [])),
            "indirect": len(radius.get("indirect", [])),
        },
    }


def fingerprint(report: dict[str, Any], model: str | None) -> str:
    """Même rapport + même modèle = même empreinte (clé du cache)."""
    payload = json.dumps({"report": report, "model": model}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
