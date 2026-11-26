# Analyste IA (pôle Sécurité et analyse)

L'analyste explique un rapport d'analyse en français et propose des actions. **Il ne décide jamais du risque** : les findings et le score viennent des règles déterministes, et le code vérifie chaque réponse du LLM.

## Utilisation

```bash
# Rapport avec explication (texte de secours si aucun LLM n'est configuré)
python -m analysis contexte.json --explain

# Explication d'un rapport déjà produit
python -m ai_analyst rapport.json

# Avec un modèle local (Ollama) : aucune donnée ne sort de la machine
CLOUDTWIN_LLM=ollama CLOUDTWIN_LLM_MODEL=llama3.1 python -m analysis contexte.json --explain

# Machine lente ou premier appel (chargement du modèle) : allonger le délai
CLOUDTWIN_LLM=ollama CLOUDTWIN_LLM_TIMEOUT=60 python -m analysis contexte.json --explain
```

```python
from ai_analyst import explain
explanation = explain(result)   # summary, recommendations[], source = "llm" | "template"
```

## Fonctionnement

1. **Rapport réduit** (`report.py`) : le LLM ne voit que les findings en échec, le score et le nombre de ressources touchées. Il ne voit jamais le jumeau ni de secret.
2. **Cache** : même rapport et même modèle donnent la même explication, sans nouvel appel.
3. **Prompt** (`prompt.py`) : règles strictes et un exemple complet (entrée et réponse attendue).
4. **Garde-fous** (`guards.py`). Une réponse est refusée si :
   - ce n'est pas du JSON au bon format ;
   - elle cite une règle absente du rapport ;
   - elle donne un score différent du score calculé ;
   - elle présente une technique MITRE comme « détectée » au lieu d'une exposition potentielle.
5. **Un seul nouvel essai** avec la raison du refus. Ensuite, ou si le LLM est en panne : **texte de secours** (`template.py`), construit à partir des remédiations des règles. Le rapport sort toujours.
6. Les règles en échec oubliées par le LLM reçoivent la recommandation du texte de secours.

Le champ `warnings` indique pourquoi une réponse du LLM a été écartée.

## Correctifs proposés par l'IA

`ai_analyst/fixes.py` : le LLM propose une nouvelle valeur pour les changements en échec, et chaque proposition est rejouée dans le jumeau (`python -m analysis contexte.json --fixes`). Elle n'est retenue que si le score baisse sans nouveau risque. Voir `docs/remediation.md`.

## Tests

`ai_analyst/tests/` : 22 tests avec un LLM simulé, sans appel réseau.
