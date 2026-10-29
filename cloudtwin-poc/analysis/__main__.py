"""Ligne de commande.

  python -m analysis contexte.json
      Analyse un contexte complet (format officiel, sortie de Hamdi).

  python -m analysis --twin outputs/current_state.json --scenario scenarios/scenario-01.json
      Analyse un scénario du PoC actuel (pont temporaire, voir adapters.py).
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
    args = p.parse_args()

    if args.context:
        ctx = load_context(args.context)
    elif args.twin and args.scenario:
        read = lambda f: json.loads(Path(f).read_text(encoding="utf-8"))
        ctx = context_from_poc(read(args.twin), read(args.scenario))
    else:
        p.error("donner un contexte, ou --twin et --scenario")

    result = run(ctx)
    print(result.model_dump_json(indent=2))
    return 1 if result.summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
