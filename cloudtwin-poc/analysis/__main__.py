"""Ligne de commande.

  python -m analysis contexte.json
      Analyse un contexte complet (format officiel, sortie de Hamdi).

  python -m analysis --twin outputs/current_state.json --scenario scenarios/scenario-01.json
      Analyse un scénario du PoC actuel (pont temporaire, voir adapters.py).

  Ajouter --explain pour l'explication de l'analyste IA (champ ai_explanation).
  Ajouter --fixes pour les correctifs proposés et vérifiés dans le jumeau (champ remediation).
  Ajouter --fail-at 80 pour bloquer une pull request quand le score atteint 80 (CI).
"""

import argparse
import json
import sys
from pathlib import Path

from analysis.adapters import context_from_poc
from analysis.engine import load_context, run


def risk_exit_code(score: int, fail_at: int) -> int:
    return 1 if score >= fail_at else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m analysis")
    p.add_argument("context", nargs="?", help="contexte d'analyse complet (JSON)")
    p.add_argument("--twin", help="current_state.json produit par discover.py")
    p.add_argument("--scenario", help="scénario au format cloudtwin-poc/scenarios")
    p.add_argument("--fail-at", type=int, metavar="SCORE",
                   help="code de sortie 1 si le score global atteint SCORE (ex. 80 pour la CI)")
    p.add_argument("--explain", action="store_true",
                   help="ajoute ai_explanation (analyste IA, ou texte de secours sans LLM)")
    p.add_argument("--fixes", action="store_true",
                   help="propose des correctifs, les rejoue dans le jumeau et affiche le score avant / après")
    args = p.parse_args()

    if args.context:
        ctx = load_context(args.context)
    elif args.twin and args.scenario:
        read = lambda f: json.loads(Path(f).read_text(encoding="utf-8"))
        ctx = context_from_poc(read(args.twin), read(args.scenario))
    else:
        p.error("donner un contexte, ou --twin et --scenario")

    result = run(ctx)
    output = result.model_dump(mode="json")
    if args.explain:
        from ai_analyst import explain   # import tardif : le moteur ne dépend pas de l'IA
        output["ai_explanation"] = explain(result).model_dump(mode="json")
    if args.fixes:
        from ai_analyst.client import from_env
        from ai_analyst.fixes import remediate_with_ai
        from analysis.remediation import summary
        report, rejected = remediate_with_ai(ctx, result, from_env())
        output["remediation"] = report.model_dump(mode="json") | {"ai_rejected": rejected}
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print(result.risk["explanation"], file=sys.stderr)
    if args.fixes:
        print(summary(report), file=sys.stderr)
    if args.fail_at is not None:
        return risk_exit_code(result.risk["score"], args.fail_at)
    return 1 if result.summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
