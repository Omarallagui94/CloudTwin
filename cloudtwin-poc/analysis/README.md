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
│   ├── resilience/        # RES-001, RES-002, RES-003
│   └── security/          # NET-001 à NET-004 ; IAM-*, K8S-* à venir
└── tests/
    ├── fixtures/          # générées avec le vrai normalizer.py (IDs et relations réels)
    ├── test_rules.py
    ├── test_engine.py
    ├── test_adapters.py
    ├── test_network_rules.py
    ├── test_res_002.py
    └── test_net_001.py
docs/rules.md              # catalogue des règles
```

## Règles actives

| ID | Gravité | Déclencheur | Statut `not_evaluated` si… |
|---|---|---|---|
| `RULE-RES-001` | HIGH (CRITICAL à 0) | Workload réduit sous 2 réplicas | nombre futur inconnu |
| `RULE-RES-003` | MEDIUM | Suppression d'une ressource dont d'autres dépendent | ressource absente du jumeau |
| `RULE-NET-003` | CRITICAL | Ingress `0.0.0.0/0` ou `::/0` sur 3306 / 5432 (TCP ou tous protocoles) | ingress futur inconnu ou règle illisible |

`RULE-NET-003` lit les deux formats d'ingress du projet : Terraform (`from_port`, `cidr_blocks`, sortie de l'ingestion) et API AWS (`FromPort`, `IpRanges`, sortie de la découverte). Elle signale aussi une exposition déjà présente avant le changement (`already_exposed_before`), distinguée de `newly_exposed`. Référence : *exposition potentielle associée à MITRE ATT&CK T1190* (jamais « T1190 détecté »).

## Ajouter une règle

1. Créer `analysis/rules/<categorie>/<id>.py` avec une classe qui hérite de `Rule`.
2. L'ajouter à `ALL_RULES` dans `analysis/rules/__init__.py`.
3. Ajouter 3 fixtures : positive, négative, données manquantes.
4. Ajouter les tests et mettre à jour `docs/rules.md`.
