# Sécurité du labo AWS

Le labo AWS + EKS sert seulement à la découverte et à la démo. Il doit rester **peu coûteux, fermé, et lu en lecture seule** par CloudTwin. Ce document donne les droits du collecteur et la checklist à suivre à chaque session.

Fichiers : `security/collector-readonly-policy.json`, `security/k8s-rbac-reader.yaml` · Tests : `security/tests/test_lab_security.py`

## 1. Le collecteur en lecture seule (moindre privilège)

Le collecteur n'a besoin que de **lire**. Sa politique IAM couvre les appels réellement effectués par `discovery/aws_collector.py`, y compris `rds:DescribeDBInstances`. Rien d'autre.

| Service | Actions autorisées |
|---|---|
| EC2 | `DescribeVpcs`, `DescribeSubnets`, `DescribeSecurityGroups`, `DescribeInstances` |
| EKS | `ListClusters`, `DescribeCluster`, `ListNodegroups`, `DescribeNodegroup` |
| ELB | `elasticloadbalancing:DescribeLoadBalancers` (ALB, NLB et Classic) |
| RDS | `DescribeDBInstances` |
| IAM | `ListRoles`, `ListAttachedRolePolicies`, `ListRolePolicies`, `GetRolePolicy`, `GetPolicy`, `GetPolicyVersion` |

- **Aucune action d'écriture**, aucun joker (`ec2:*`, `*`).
- **Verrouillée sur la région du labo** (`eu-west-3`) pour EC2, EKS, ELB et RDS. IAM est un service global, donc sans condition de région.
- **CloudTwin se vérifie lui-même** : les tests passent la politique dans RULE-IAM-001 et RULE-IAM-002, qui ne doivent rien trouver.

Les tests échouent si le collecteur appelle une action absente de la politique, ou si la politique autorise une action jamais appelée. La politique ne peut donc pas dériver du code.

## 2. Le collecteur Kubernetes

- **Mapping EKS `aws-auth`** du rôle du collecteur vers le groupe Kubernetes `cloudtwin-rbac-readers`, conformément au module EKS v19 utilisé par le projet.
- **ClusterRole `cloudtwin-rbac-reader`** (`security/k8s-rbac-reader.yaml`) lié à ce groupe. Il lit les ressources prises en charge par le collecteur, ainsi que les ClusterRoles et ClusterRoleBindings, sans lire les Secrets.

## 3. Mise en place (pôle Cloud)

La configuration est définie dans `terraform/eks/minimal/collector-security.tf`. Elle est désactivée par défaut. Un plan approuvé doit fournir :

```hcl
collector_security_enabled        = true
collector_trusted_principal_arns = ["arn:aws:iam::<account-id>:role/<operator-or-ci-role>"]
```

Terraform crée alors le rôle IAM en lecture seule, l'ajoute au groupe `cloudtwin-rbac-readers` via la ConfigMap `aws-auth` gérée par le module EKS v19 existant, puis crée le ClusterRole et son binding. Le manifeste `security/k8s-rbac-reader.yaml` reste la représentation Kubernetes autonome équivalente; ne pas appliquer les deux modes au même cluster.

Le projet reste volontairement en mode d'authentification EKS `CONFIG_MAP`. Passer aux access entries nécessiterait une migration majeure du module EKS v19 vers v20 et un passage irréversible vers `API_AND_CONFIG_MAP`; cette migration n'est pas nécessaire pour le collecteur et n'est pas incluse dans ce changement.

### État de validation

- Défini dans Terraform et couvert par les tests statiques : oui.
- Appliqué à un labo AWS/EKS : non.
- Vérifié avec l'identité réelle du collecteur : non.

Ne pas présenter cette configuration comme déployée avant un `terraform plan` revu, une approbation explicite, l'application temporaire, les contrôles de permissions et la destruction vérifiée du labo.

### Validation live après approbation

1. Examiner le plan et les ressources payantes (EKS, EC2, NAT/ELB/EBS éventuels).
2. Appliquer uniquement le labo approuvé.
3. Vérifier `aws sts get-caller-identity`, puis lancer la découverte en lecture seule.
4. Vérifier `kubectl auth can-i list pods --all-namespaces` et `kubectl auth can-i list clusterrolebindings`.
5. Vérifier que `kubectl auth can-i get secrets --all-namespaces` et les verbes d'écriture répondent `no`.
6. Capturer le Current Twin et valider RDS, IAM, security contexts et RBAC.
7. Exécuter `terraform destroy`, puis vérifier EKS, EC2, Load Balancers, NAT Gateways, EBS, Elastic IPs et RDS.

## 4. Checklist à chaque session de labo

| # | Point | Comment c'est vérifié |
|---|---|---|
| 1 | Aucun secret AWS dans Git (clés `AKIA…`, `aws_secret_access_key`, clés privées) | Automatique : `test_no_aws_secret_is_committed` |
| 2 | `.tfstate`, `.tfvars`, `.env` et `.terraform/` ignorés par Git | Automatique : `test_sensitive_files_are_ignored_by_git` |
| 3 | Le collecteur utilise le rôle `cloudtwin-collector-readonly`, jamais un compte administrateur | Manuel : `aws sts get-caller-identity` avant la découverte |
| 4 | Alerte de budget AWS active (par exemple 20 USD par mois, alerte à 80 %) | Manuel : console AWS Budgets |
| 5 | Aucun Security Group du labo n'ouvre SSH (22) ou RDP (3389) à `0.0.0.0/0` | CloudTwin : RULE-NET-001 sur le jumeau découvert |
| 6 | MFA activé sur le compte root, et root jamais utilisé | Manuel : console IAM |
| 7 | Identifiants AWS seulement dans `~/.aws/credentials` ou des variables d'environnement, jamais dans le code | Manuel, et le point 1 |
| 8 | `terraform destroy` à la fin de chaque session (EKS, ALB et RDS coûtent à l'heure) | Manuel : vérifier qu'il ne reste aucune ressource facturée |

Le point 5 montre CloudTwin appliqué à son propre labo : la découverte suivie de l'analyse doit donner `pass` pour NET-001.
