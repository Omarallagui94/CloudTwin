# Simulation : Future Twin et Diff (tâches H5 et H6)

La simulation applique un changement proposé à une **copie** du jumeau, sans jamais toucher à AWS, puis compare les deux états. C'est le maillon qui manquait au Milestone 1.

```text
Current Twin + tfplan.json / manifest YAML / scénario
   → ingestion (ingestion/)  → Future Twin (future_twin.py)  → Diff (diff.py)  → analyse (analysis/)
```

## Utilisation

```bash
python -m simulation --twin scenarios/S04-open-security-group/current-twin.json \
                     --change scenarios/S04-open-security-group/proposed-plan.json
# options : --future-out future.json  --explain  --fixes  --fail-at 80
```

```python
from simulation import build_future_twin, diff_twins
future = build_future_twin(current_twin, changeset)   # future.twin, .changed, .created, .deleted, .warnings
diff = diff_twins(current_twin, future.twin)          # conforme à schema/v1/diff.schema.json
```

## Règles d'application

| Opération | Effet sur le Future Twin |
|---|---|
| `update` | La propriété visée (`properties.a.b`) prend la valeur `after`. Les propriétés combinées des scénarios (`service_type_and_scheme`) sont réparties sur les vrais champs. |
| `replace` | Toutes les propriétés sont remplacées par `after`. |
| `create` | Nouvelle ressource au même format, sans relation (le plan ne dit pas à quoi elle est reliée). |
| `delete` | La ressource et toutes ses relations sont retirées. |

- **Même format que le Current Twin.** Le schéma twin v1 interdit tout champ en plus sur une ressource. Le marqueur « modifié » de la présentation vit donc à côté : dans `future.changed` et dans le Diff. Un jumeau valide en entrée reste valide en sortie, ce que les tests vérifient.
- **Le jumeau d'entrée n'est jamais modifié.**
- **Les incohérences deviennent des avertissements :** une valeur `before` différente de l'état du jumeau, ou une ressource absente.

## Diff

Une entrée par ressource `ADDED` ou `REMOVED`, et une entrée par propriété `MODIFIED`, avec l'avant et l'après. Les dictionnaires sont comparés clé par clé ; les listes (règles d'ingress, politiques) sont comparées en entier. Les relations retirées avec une ressource supprimée sont données à part (`removed_relationships`), car le schéma diff v1 ne décrit que les ressources.

## Lien avec l'analyse

`integration/hamdi_fatma.py` construit maintenant le Future Twin et le passe dans `AnalysisContext.future_twin`. Il accepte aussi les créations, puisqu'une ressource créée n'est pas encore dans le Current Twin.

## Reste à faire

- Stocker le Future Twin dans Neo4j (`storage/neo4j_store.py`) à côté du Current Twin, pour l'interface.
- Déduire les relations d'une ressource créée (par exemple un Security Group rattaché à son VPC via `vpc_id`).
