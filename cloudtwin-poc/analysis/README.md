# CloudTwin AI — Moteur d'analyse (pôle Sécurité et Analyse)

Moteur déterministe qui lit un changement simulé et produit des findings de sécurité et de résilience.

## Installation

```bash
pip install "pydantic>=2" pytest
```

## Lancer les tests

```bash
python -m pytest -q
```

## Analyser une simulation

Depuis le dossier `cloudtwin-poc/` :

```bash
# Avec les vrais fichiers du PoC (après discover.py)
python -m analysis --twin outputs/current_state.json --scenario scenarios/scenario-01.json

# Avec un contexte complet (format officiel, future sortie de Hamdi)
python -m analysis analysis/tests/fixtures/res001/positive_3_to_1.json
```

Code de sortie : `0` si aucun `fail`, `1` sinon (utilisable dans la CI pour bloquer une PR).

## Structure

```text
analysis/
├── models.py              # contrat d'entrée / sortie (Pydantic)
├── engine.py              # exécute les règles, trie et résume
├── adapters.py            # pont temporaire : current_state.json + scénario du PoC
├── __main__.py            # ligne de commande
├── rules/
│   ├── __init__.py        # registre des règles actives
│   ├── base.py            # classe Rule commune
│   ├── resilience/        # RES-001, RES-003
│   └── security/          # NET-*, IAM-*, K8S-* (à venir)
└── tests/
    ├── fixtures/          # générées avec le vrai normalizer.py (IDs et relations réels)
    ├── test_rules.py
    ├── test_engine.py
    └── test_adapters.py
docs/rules.md              # catalogue des règles
```

## Ajouter une règle

1. Créer `analysis/rules/<categorie>/<id>.py` avec une classe qui hérite de `Rule`.
2. L'ajouter à `ALL_RULES` dans `analysis/rules/__init__.py`.
3. Ajouter 3 fixtures : positive, négative, données manquantes.
4. Ajouter les tests et mettre à jour `docs/rules.md`.
