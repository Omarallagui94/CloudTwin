# Score global de risque

Le score résume tous les findings d'une simulation en une note de 0 à 100. Il est **déterministe** : mêmes findings, même score. Le jury peut le recalculer à la main à partir du rapport.

Code : `analysis/scoring/score.py` · Tests : `analysis/tests/test_scoring.py`

## Formule

```text
score = min(100, Σ poids(gravité) + bonus_blast_radius)

poids  : CRITICAL = 30 · HIGH = 20 · MEDIUM = 10 · LOW = 5
bonus  : +1 par ressource indirectement impactée (sans doublon), plafonné à 10
niveau : 0–29 Faible · 30–59 Moyen · 60–79 Élevé · 80–100 Critique
```

## Règles de comptage

1. **Seuls les findings `fail` comptent.** `pass` et `not_evaluated` valent 0.
2. **Un même risque n'est compté qu'une fois.** NET-001, NET-002 et NET-003 peuvent décrire la même ouverture d'un Security Group. Sur un même changement, seul le finding le plus grave compte. Les autres restent visibles dans le rapport avec `counted: false`.
3. **RULE-IAM-003 compte en plus** des règles locales. Le chemin créé par la combinaison des changements est un risque distinct de chacun de ses maillons.
4. **Un score avec des `not_evaluated` est un minimum** (`partial: true`). Le risque réel peut être plus élevé, puisque certaines règles n'ont pas pu conclure.

## Dans le rapport

Le résultat d'analyse contient un bloc `risk` :

```json
{
  "score": 80,
  "level": "critical",
  "label": "Critique",
  "partial": false,
  "findings_points": 80,
  "blast_radius_bonus": 0,
  "lines": [{ "rule_id": "RULE-RES-001", "severity": "high", "weight": 20, "counted": true }],
  "explanation": "RULE-IAM-002 HIGH +20 + RULE-NET-002 HIGH +20 + RULE-RES-001 HIGH +20 + RULE-RES-002 HIGH +20 = 80/100 → Critique"
}
```

## Scénario de démonstration (présentation §11)

| Changement | Règle | Gravité | Points |
|---|---|---|---|
| API : 3 → 1 réplica | RULE-RES-001 | HIGH | 20 |
| RDS Multi-AZ : true → false | RULE-RES-002 | HIGH | 20 |
| Security Group : 10.0.0.0/16 → 0.0.0.0/0 | RULE-NET-002 | HIGH | 20 |
| Ajout de `iam:PassRole` sur `*` | RULE-IAM-002 | HIGH | 20 |
| **Total** | | | **80/100 → Critique** |

Si l'exposition passe par le Service (LoadBalancer internet-facing) au lieu du Security Group, RULE-IAM-003 détecte en plus le chemin `Internet → Service → Pod → ServiceAccount → rôle (iam:PassRole)`, et le score atteint 100.

> La présentation annonçait « 89/100 ». Avec la formule ci-dessus, le scénario donne **80/100**, toujours au niveau Critique. Le score de la slide est à mettre à jour.

## Dans la CI

```bash
python -m analysis contexte.json --fail-at 80
```

Le code de sortie vaut `1` si le score atteint 80 : la pull request d'infrastructure est bloquée. Le calcul s'affiche sur la sortie d'erreur.
