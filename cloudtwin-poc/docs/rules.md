# Catalogue des règles d'analyse

Le moteur applique 10 règles déterministes à chaque simulation. Chaque règle est une fonction pure : même contexte, mêmes findings. Elle ne parle ni à AWS, ni à Neo4j, ni au LLM.

Code : `analysis/rules/` · Tests : `analysis/tests/` · Score : `docs/scoring.md`

## Vue d'ensemble

| Règle | Ce qu'elle détecte | Gravité | Données lues | Référence |
|---|---|---|---|---|
| RULE-NET-001 | SSH (22) ou RDP (3389) ouvert à `0.0.0.0/0` ou `::/0` | HIGH | Ingress d'un `SECURITY_GROUP` | CIS AWS Foundations v4.0.1 5.3 / 5.4 ; T1190 |
| RULE-NET-002 | Security Group élargi à Internet, quel que soit le port | HIGH | Ingress d'un `SECURITY_GROUP` | T1190 |
| RULE-NET-003 | Port de base de données (3306, 5432) ouvert à Internet | CRITICAL | Ingress d'un `SECURITY_GROUP` | T1190 |
| RULE-NET-004 | Service Kubernetes qui devient un LoadBalancer internet-facing | HIGH | Type, schéma, annotations d'un `SERVICE` | T1190 |
| RULE-RES-001 | Workload réduit à moins de 2 réplicas | HIGH | `replicas` d'un `DEPLOYMENT` / `STATEFUL_SET` | — |
| RULE-RES-002 | RDS `multi_az` qui passe de true à false | HIGH | `multi_az` d'un `RDS_INSTANCE` | AWS Security Hub RDS.5 ; CIS AWS Foundations v4.0.1 2.2.4 |
| RULE-RES-003 | Suppression d'une ressource dont d'autres dépendent | MEDIUM | Relations du jumeau | — |
| RULE-IAM-001 | Politique `*:*` (ou AdministratorAccess) | CRITICAL | Politiques d'un `IAM_ROLE` | CIS AWS Foundations v4.0.1 1.16 ; AWS Security Hub IAM.1 |
| RULE-IAM-002 | `iam:PassRole` ou une autre action d'escalade de privilèges | HIGH | Politiques d'un `IAM_ROLE` | T1098.003 |
| RULE-IAM-003 | Nouveau chemin Internet → rôle IAM dangereux | CRITICAL | Graphe complet avant / après | T1098.003 ; T1190 |

T1190 et T1098.003 sont des techniques MITRE ATT&CK. Le rapport écrit toujours « exposition potentielle associée à… », jamais « T1190 détecté » : un port ouvert est une exposition, pas une attaque.

## Statuts communs

Chaque règle renvoie un finding par élément évalué :

- **`fail`** : le risque est présent. La gravité indiquée est celle d'un risque **créé** par le changement. Un risque qui existait déjà et reste en place est signalé en MEDIUM (NET-001, NET-002, NET-004, IAM-001, IAM-002).
- **`pass`** : la règle a été évaluée, et il n'y a pas de risque.
- **`not_evaluated`** : une donnée manque (valeur `after_unknown` d'un plan Terraform, politique au contenu inconnu, schéma de load balancer absent…). La règle ne conclut pas, et ne renvoie **jamais** un faux `pass`.
- Une liste vide signifie que la règle ne s'applique à aucun changement de la simulation.

## Formats acceptés

Les règles réseau et IAM lisent les deux sources du jumeau :

| Donnée | Découverte (Omar, API AWS) | Ingestion (Hamdi, Terraform) |
|---|---|---|
| Ingress d'un Security Group | `ingress_rules` : `IpProtocol`, `FromPort`, `ToPort`, `IpRanges[].CidrIp`, `Ipv6Ranges[].CidrIpv6` | `ingress` : `protocol`, `from_port`, `to_port`, `cidr_blocks`, `ipv6_cidr_blocks` |
| Politiques d'un rôle IAM | `attached_policies[]` et `inline_policies[]` avec `document` | `inline_policy[]` (JSON en texte) et `managed_policy_arns[]` |
| Multi-AZ RDS | `MultiAZ` | `multi_az` |

## Détail des règles

### RULE-NET-001 — Port d'administration ouvert à Internet
- **Échec** : le changement ouvre 22 (SSH) ou 3389 (RDP) à une source publique, en TCP ou « tous protocoles ». Une plage large qui couvre ces ports (par exemple 0–1024) compte aussi.
- **Exemple** : `tcp 22 depuis 0.0.0.0/0` sur le Security Group des nœuds EKS donne HIGH. L'EC2 et le cluster EKS sont marqués impactés.
- **Remédiation** : Session Manager ou un bastion, ou une source d'administration précise.

### RULE-NET-002 — Security Group ouvert à Internet
- **Échec** : le changement ajoute au moins une entrée `0.0.0.0/0` ou `::/0`.
- **Exception** : 80/443 ouverts sur le Security Group d'un load balancer internet-facing. C'est son rôle, donc `pass`.
- **Chevauchement** : `evidence.overlaps_with` indique si NET-001 ou NET-003 décrivent la même ouverture. Le score ne la compte qu'une fois.
- **Exemple** : S04 (5432 ouvert à `0.0.0.0/0`) donne HIGH, avec `overlaps_with = ["RULE-NET-003"]`.

### RULE-NET-003 — Base de données ouverte à Internet
- **Échec** : une source publique atteint 3306 (MySQL) ou 5432 (PostgreSQL), en TCP ou « tous protocoles ».
- **Exemple** : S04 donne CRITICAL. C'est la chaîne du Milestone 1, de bout en bout par l'ingestion.

### RULE-NET-004 — Service exposé à Internet
- **Échec** : un Service devient un `LoadBalancer` internet-facing. Le schéma est lu dans le changement, dans les annotations, ou déduit du jumeau quand un load balancer public route déjà vers le Service.
- **Pass** : `ClusterIP`, `ExternalName`, LoadBalancer `internal`, ou limité par `loadBalancerSourceRanges`.
- **`not_evaluated`** : `NodePort` (dépend des Security Groups des nœuds), ou LoadBalancer sans schéma connu.
- **Exemple** : S03 (`ClusterIP` → `LoadBalancer` internet-facing) donne HIGH.

### RULE-RES-001 — Point unique de défaillance
- **Échec** : les réplicas diminuent et passent sous 2. Le passage à 0 donne CRITICAL, car le service s'arrête.
- **Exemple** : S02 (`ui` 3 → 1) donne HIGH. Le Service `ui` est marqué impacté.

### RULE-RES-002 — Perte de redondance RDS
- **Échec** : `multi_az` passe de true à false (HIGH), ou une nouvelle base est créée sans Multi-AZ (MEDIUM).
- **Ignoré** : les instances membres d'un cluster (Aurora), dont la disponibilité se règle au niveau du cluster.
- **Limite** : RDS n'est pas encore collecté, et le type `RDS_INSTANCE` reste à ajouter au schéma v1.

### RULE-RES-003 — Suppression d'une ressource utilisée
- **Échec** : une ressource supprimée a encore des dépendants qui ne sont pas supprimés eux aussi.
- **Sens des relations** : `DEPENDENCY_DIRECTION`, le même tableau que le blast radius. Par exemple, « LB ROUTES_TO Service » veut dire que le LB dépend du Service.
- **`not_evaluated`** : relation de type inconnu. Par exemple, `DEPENDS_ON` dans S05 n'est pas encore dans le schéma.

### RULE-IAM-001 — Politique IAM trop permissive
- **Échec** : une déclaration `Allow` avec `Action: "*"` sur `Resource: "*"` est ajoutée, en ligne ou via `AdministratorAccess`.
- **Exemple** : un plan Terraform qui ajoute `*:*` au rôle `eks-node` donne CRITICAL. Le node group EKS est marqué impacté.

### RULE-IAM-002 — Permission IAM sensible ajoutée
- **Échec** : une des 14 actions d'escalade est accordée (`iam:PassRole`, `iam:PutRolePolicy`, `iam:CreatePolicyVersion`, `iam:AttachRolePolicy`…). HIGH si la ressource est large (`*`), MEDIUM si elle vise un ARN précis.
- **Jokers** : `iam:*`, `iam:Pass*` et `NotAction` sont pris en compte.
- **Pas de double comptage** : un `*:*` complet est laissé à IAM-001.

### RULE-IAM-003 — Nouveau chemin d'escalade depuis Internet
- **Principe** : la règle construit le graphe d'attaque avant et après le changement, et cherche les **nouveaux** chemins depuis Internet vers un rôle « dangereux » (un rôle sur lequel IAM-001 ou IAM-002 se déclencherait) :
  ```text
  Internet → LoadBalancer → Service → Pod → ServiceAccount → (IRSA) → rôle IAM        CRITICAL
  … → Pod → Node → EC2 → NodeGroup → rôle du nœud (IMDS)                              HIGH
  ```
- **Combinaison** : un Service public seul, ou `iam:PassRole` seul, donnent `pass`. Les deux ensemble donnent CRITICAL. Le finding donne le chemin complet, arête par arête.
- **Limite** : l'exposition par un Security Group (nœuds en sous-réseau public) n'est pas encore modélisée.

## Ajouter une règle

1. Créer `analysis/rules/<catégorie>/<id>.py`, avec une classe qui hérite de `Rule`.
2. L'ajouter à `ALL_RULES` dans `analysis/rules/__init__.py`.
3. Écrire au moins un test positif, un négatif et un cas de données manquantes.
4. Vérifier la référence CIS ou MITRE à la source, puis compléter ce catalogue.
