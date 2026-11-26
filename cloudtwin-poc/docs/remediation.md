# Correctifs vérifiés par le jumeau

CloudTwin ne se contente pas de dire qu'un changement est dangereux. Il **propose des correctifs, les rejoue dans le jumeau et montre le score avant / après**. L'humain choisit.

Code : `analysis/remediation/` (catalogue et rejeu), `ai_analyst/fixes.py` (propositions de l'IA) · Tests : `analysis/tests/test_remediation.py`, `ai_analyst/tests/test_fixes.py`

```bash
python -m analysis contexte.json --fixes          # catalogue seul
CLOUDTWIN_LLM=ollama python -m analysis contexte.json --fixes   # + propositions de l'IA
```

## Le principe : l'IA propose, les règles vérifient, l'humain choisit

1. **Proposer.** Pour chaque règle en échec, le catalogue propose une nouvelle valeur pour le changement en cause. Si un LLM est configuré, il peut proposer ses propres correctifs.
2. **Rejouer.** Chaque correctif est appliqué à une copie du contexte, puis tout le moteur est relancé : blast radius, règles, score. Le contexte d'origine n'est jamais modifié.
3. **Vérifier.** Un correctif est **vérifié** seulement s'il fait baisser le score **sans créer de nouveau risque**. Une proposition de l'IA qui ne remplit pas ces conditions est affichée comme rejetée.
4. **Classer.** Les options vont de la plus efficace à la moins efficace, suivies de « tous les correctifs vérifiés ensemble ».

## Le catalogue

| Règle | Correctif proposé |
|---|---|
| NET-001, NET-002, NET-003 | Remplacer `0.0.0.0/0` par le CIDR du VPC du Security Group (lu dans le jumeau), et retirer `::/0`. Une seule option pour les trois règles. |
| NET-004 | Garder le Service en `ClusterIP`, ou passer le load balancer en `internal` |
| RES-001 | Garder au moins 2 réplicas (ou la valeur d'avant, si elle était plus haute) |
| RES-002 | Garder Multi-AZ activé |
| RES-003 | Annuler la suppression |
| IAM-001, IAM-002 | Retirer les déclarations `*:*` ou d'escalade, et garder les autres ; retirer AdministratorAccess ou IAMFullAccess |
| K8S-001 | Retirer `privileged: true` des conteneurs et garder l'ancien ServiceAccount ; annuler un ClusterRoleBinding vers `cluster-admin` |
| IAM-003 | Pas de correctif propre : on coupe le chemin en corrigeant l'un de ses maillons |

## Exemple : le scénario de démo

```text
Score actuel : 100/100
✔ Garder le Service api en ClusterIP (interne)            → 60/100 (-40)
✔ Retirer les permissions d'escalade (iam:PassRole…)      → 60/100 (-40)
✔ Garder 3 réplicas pour l'API                            → 90/100 (-10)
✔ Garder Multi-AZ activé sur la base                      → 90/100 (-10)
Tous les correctifs vérifiés                              → 0/100 (Faible)
```

Le classement montre où agir en premier. Garder le Service interne fait gagner 40 points, parce qu'il résout NET-004 **et** coupe le chemin d'attaque IAM-003. Corriger les réplicas ne fait gagner que 10 points.

## Les garde-fous sur les propositions de l'IA

- Le LLM ne voit que les changements en échec (ressource, propriété, avant, après), jamais le jumeau.
- Une proposition sur un changement inconnu ou sans risque est écartée.
- Une proposition d'un autre type que la valeur d'origine est écartée (par exemple un texte à la place d'un nombre de réplicas).
- Toute proposition restante est rejouée : si elle ne fait pas baisser le score, ou si elle crée un nouveau risque (par exemple fermer 8080 mais ouvrir SSH), elle est affichée comme rejetée.
- Si le LLM est indisponible, le catalogue suffit.
