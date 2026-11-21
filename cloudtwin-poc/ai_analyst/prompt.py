"""Prompt de l'analyste IA. Le LLM explique ; il ne décide jamais du risque."""

from __future__ import annotations

import json
from typing import Any

SYSTEM = """Tu es l'analyste de CloudTwin AI, un jumeau numérique qui simule des changements AWS et Kubernetes avant leur déploiement.

Tu reçois un rapport JSON produit par un moteur de règles déterministe. Ton rôle est de l'expliquer à un ingénieur DevOps, en français, et de proposer des actions.

Règles strictes :
1. Tu ne crées aucun risque : tu ne parles que des risques du champ "risks", avec leur rule_id exact.
2. Tu ne changes jamais le score. Si tu le cites, c'est exactement la valeur du champ "score", sur 100.
3. Une exposition n'est pas une attaque. Écris « exposition potentielle associée à T1190 », jamais « T1190 détecté ».
4. Si plusieurs risques se combinent (par exemple un service exposé et un rôle IAM trop puissant), explique d'abord cette combinaison.
5. Si "partial" vaut true, dis que le score est un minimum, car certaines règles n'ont pas pu conclure.
6. Une recommandation par rule_id en échec, concrète et courte.

Réponds uniquement avec un objet JSON de la forme :
{"summary": "<3 à 5 phrases>", "recommendations": [{"rule_id": "RULE-XXX-000", "action": "<action>"}]}"""

EXAMPLE_REPORT = {
    "score": 40, "label": "Moyen", "partial": False,
    "risks": [
        {"rule_id": "RULE-RES-001", "severity": "high", "title": "Point unique de défaillance",
         "description": "k8s:deployment:prod:api passe de 3 à 1 réplica(s).",
         "remediation": "Garder au moins 2 réplicas en production.", "reference": None},
        {"rule_id": "RULE-NET-004", "severity": "high", "title": "Service exposé à Internet",
         "description": "k8s:service:prod:api devient joignable depuis Internet (LoadBalancer internet-facing).",
         "remediation": "Garder le Service en ClusterIP derrière un Ingress contrôlé.",
         "reference": "Exposition potentielle associée à MITRE ATT&CK T1190"},
    ],
    "not_evaluated": [], "blast_radius": {"direct": 1, "indirect": 2},
}
EXAMPLE_ANSWER = {
    "summary": ("Ce changement obtient 40/100 (Moyen). L'API devient joignable depuis Internet au moment "
                "où elle n'a plus qu'un seul réplica : une simple panne ou une surcharge la rend indisponible "
                "pour tous ses utilisateurs. L'exposition est potentiellement associée à T1190."),
    "recommendations": [
        {"rule_id": "RULE-NET-004", "action": "Garder le Service api en ClusterIP et l'exposer via un Ingress."},
        {"rule_id": "RULE-RES-001", "action": "Conserver au moins 2 réplicas de l'API en production."},
    ],
}


def messages(report: dict[str, Any], feedback: str | None = None) -> list[dict[str, str]]:
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(EXAMPLE_REPORT, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(EXAMPLE_ANSWER, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(report, ensure_ascii=False)},
    ]
    if feedback:
        msgs.append({"role": "user", "content": f"Ta réponse a été refusée : {feedback}. Corrige-la."})
    return msgs
