# Sécurité du labo AWS

Le labo AWS + EKS sert seulement à la découverte et à la démo. Il doit rester **peu coûteux, fermé, et lu en lecture seule** par CloudTwin. Ce document donne les droits du collecteur et la checklist à suivre à chaque session.

Fichiers : `security/collector-readonly-policy.json`, `security/k8s-rbac-reader.yaml` · Tests : `security/tests/test_lab_security.py`

## 1. Le collecteur en lecture seule (moindre privilège)

Le collecteur n'a besoin que de **lire**. Sa politique IAM autorise exactement les 15 appels que fait `discovery/aws_collector.py`, plus `rds:DescribeDBInstances` pour le futur collecteur RDS (RULE-RES-002). Rien d'autre.

| Service | Actions autorisées |
|---|---|
| EC2 | `DescribeVpcs`, `DescribeSubnets`, `DescribeSecurityGroups`, `DescribeInstances` |
| EKS | `ListClusters`, `DescribeCluster`, `ListNodegroups`, `DescribeNodegroup` |
| ELB | `elasticloadbalancing:DescribeLoadBalancers` (ALB, NLB et Classic) |
| RDS | `DescribeDBInstances` (prévu) |
| IAM | `ListRoles`, `ListAttachedRolePolicies`, `ListRolePolicies`, `GetRolePolicy`, `GetPolicy`, `GetPolicyVersion` |

- **Aucune action d'écriture**, aucun joker (`ec2:*`, `*`).
- **Verrouillée sur la région du labo** (`eu-west-3`) pour EC2, EKS, ELB et RDS. IAM est un service global, donc sans condition de région.
- **CloudTwin se vérifie lui-même** : les tests passent la politique dans RULE-IAM-001 et RULE-IAM-002, qui ne doivent rien trouver.

Les tests échouent si le collecteur appelle une action absente de la politique, ou si la politique autorise une action jamais appelée. La politique ne peut donc pas dériver du code.

## 2. Le collecteur Kubernetes

- **Access entry EKS** du rôle du collecteur, avec la politique d'accès `AmazonEKSViewPolicy`. Ne **pas** utiliser `AmazonEKSAdminViewPolicy` : elle donne aussi la lecture des Secrets Kubernetes.
- **Groupe Kubernetes `cloudtwin-rbac-readers`** sur cette access entry, lié au ClusterRole `cloudtwin-rbac-reader` (`security/k8s-rbac-reader.yaml`). Il ne lit que les rôles et les bindings RBAC, ce dont RULE-K8S-001 a besoin pour détecter `cluster-admin`.

## 3. Mise en place (pôle Cloud)

Extrait Terraform à adapter dans le labo. Il n'a pas pu être validé ici : lancer `terraform validate` avant de l'appliquer.

```hcl
resource "aws_iam_role" "cloudtwin_collector" {
  name               = "cloudtwin-collector-readonly"
  assume_role_policy = data.aws_iam_policy_document.collector_trust.json  # qui peut prendre ce rôle
}

resource "aws_iam_role_policy" "cloudtwin_collector" {
  name   = "cloudtwin-collector-readonly"
  role   = aws_iam_role.cloudtwin_collector.id
  policy = file("${path.module}/../../cloudtwin-poc/security/collector-readonly-policy.json")
}

resource "aws_eks_access_entry" "cloudtwin_collector" {
  cluster_name      = module.retail_app_eks.eks_cluster_id
  principal_arn     = aws_iam_role.cloudtwin_collector.arn
  kubernetes_groups = ["cloudtwin-rbac-readers"]
}

resource "aws_eks_access_policy_association" "cloudtwin_collector_view" {
  cluster_name  = module.retail_app_eks.eks_cluster_id
  principal_arn = aws_iam_role.cloudtwin_collector.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy"
  access_scope { type = "cluster" }
}
```

Puis : `kubectl apply -f cloudtwin-poc/security/k8s-rbac-reader.yaml`.

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
