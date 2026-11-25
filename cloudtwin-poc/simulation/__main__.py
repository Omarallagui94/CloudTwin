"""Ligne de commande de la simulation (Milestone 1).

  python -m simulation --twin current-twin.json --change proposed-plan.json
  python -m simulation --twin current-twin.json --change proposed-service.yaml --explain --fixes
  python -m simulation ... --future-out future-twin.json    # écrit aussi le Future Twin

Le rapport JSON part sur la sortie standard ; un résumé lisible sur la sortie d'erreur.
"""

import argparse
import json
import sys
from pathlib import Path

from simulation.pipeline import simulate


def main() -> int:
    # Windows often inherits a legacy cp1252 console. The JSON contracts and
    # human summaries contain Unicode, so make CLI output deterministic too.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m simulation")
    p.add_argument("--twin", required=True, help="Current Twin (JSON)")
    p.add_argument("--change", required=True, help="tfplan.json, manifest YAML ou scénario JSON")
    p.add_argument("--kind", choices=["terraform", "kubernetes", "scenario"], help="forcer le format")
    p.add_argument("--future-out", help="fichier où écrire le Future Twin")
    p.add_argument("--explain", action="store_true", help="ajoute l'explication de l'analyste IA")
    p.add_argument("--fixes", action="store_true", help="ajoute les correctifs vérifiés dans le jumeau")
    p.add_argument("--fail-at", type=int, metavar="SCORE", help="code 1 si le score atteint SCORE")
    args = p.parse_args()

    out = simulate(args.twin, args.change, kind=args.kind)
    context, result = out.pop("_context"), out.pop("_result")
    if args.explain:
        from ai_analyst import explain
        out["ai_explanation"] = explain(result).model_dump(mode="json")
    if args.fixes:
        from ai_analyst.client import from_env
        from ai_analyst.fixes import remediate_with_ai
        report, rejected = remediate_with_ai(context, result, from_env())
        out["remediation"] = report.model_dump(mode="json") | {"ai_rejected": rejected}
    if args.future_out:
        Path(args.future_out).write_text(json.dumps(out["future_twin"], indent=2, ensure_ascii=False),
                                         encoding="utf-8")

    print(json.dumps(out, indent=2, ensure_ascii=False))
    modified = sum(1 for c in out["diff"]["changes"] if c["change_type"] == "MODIFIED")
    print(f"{len(out['changes']['changes'])} changement(s) → Future Twin : "
          f"{modified} propriété(s) modifiée(s), {len(out['simulation']['created'])} créée(s), "
          f"{len(out['simulation']['deleted'])} supprimée(s)", file=sys.stderr)
    for warning in out["simulation"]["warnings"]:
        print(f"attention : {warning}", file=sys.stderr)
    print(result.risk["explanation"], file=sys.stderr)
    if args.fail_at is not None:
        return 1 if result.risk["score"] >= args.fail_at else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
