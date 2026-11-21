"""Ligne de commande.

  python -m analysis contexte.json
      Analyse un contexte complet (format officiel, sortie de Hamdi).

  python -m analysis --twin outputs/current_state.json --scenario scenarios/scenario-01.json
      Analyse un scénario du PoC actuel (pont temporaire, voir adapters.py).

  Ajouter --explain pour l'explication de l'analyste IA (champ ai_explanation).
  Ajouter --fail-at 80 pour bloquer une pull request quand le score atteint 80 (CI).
"""

import argparse
import json
import sys
from pathlib import Path

from analysis.adapters import context_from_poc
from analysis.engine import load_context, run


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m analysis")
    p.add_argument("context", nargs="?", help="contexte d'analyse complet (JSON)")
    p.add_argument("--twin", help="current_state.json produit par discover.py")
    p.add_argument("--scenario", help="scénario au format cloudtwin-poc/scenarios")
    p.add_argument("--fail-at", type=int, metavar="SCORE",
                   help="code de sortie 1 si le score global atteint SCORE (ex. 80 pour la CI)")
    p.add_argument("--explain", action="store_true",
                   help="ajoute ai_explanation (analyste IA, ou texte de secours sans LLM)")
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
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print(result.risk["explanation"], file=sys.stderr)
    if args.fail_at is not None:
        return 1 if result.risk["score"] >= args.fail_at else 0
    return 1 if result.summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
