"""Ligne de commande.

  python -m analysis contexte.json > rapport.json
  python -m ai_analyst rapport.json

Sans configuration, l'explication de secours (sans IA) est produite.
Avec un modèle local : CLOUDTWIN_LLM=ollama CLOUDTWIN_LLM_MODEL=llama3.1 python -m ai_analyst rapport.json
"""

import argparse
import json
import sys
from pathlib import Path

from ai_analyst import explain


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m ai_analyst")
    p.add_argument("report", help="résultat JSON de python -m analysis")
    args = p.parse_args()
    result = json.loads(Path(args.report).read_text(encoding="utf-8"))
    print(explain(result).model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
