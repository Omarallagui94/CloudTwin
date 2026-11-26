data "aws_iam_policy_document" "cloudtwin_collector_trust" {
  count = var.collector_security_enabled ? 1 : 0

  statement {
    sid     = "AllowExplicitCollectorPrincipals"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.collector_trusted_principal_arns
    }
  }
}

resource "aws_iam_role" "cloudtwin_collector" {
  count = var.collector_security_enabled ? 1 : 0

  name               = "${var.environment_name}-cloudtwin-collector-readonly"
  assume_role_policy = data.aws_iam_policy_document.cloudtwin_collector_trust[0].json
  tags               = module.tags.result
}

resource "aws_iam_role_policy" "cloudtwin_collector" {
  count = var.collector_security_enabled ? 1 : 0

  name   = "cloudtwin-collector-readonly"
  role   = aws_iam_role.cloudtwin_collector[0].id
  policy = file("${path.module}/../../../cloudtwin-poc/security/collector-readonly-policy.json")
}

resource "kubernetes_cluster_role_v1" "cloudtwin_collector" {
  count = var.collector_security_enabled ? 1 : 0

  metadata {
    name = "cloudtwin-rbac-reader"
  }

  rule {
    api_groups = [""]
    resources  = ["namespaces", "nodes", "pods", "services", "serviceaccounts"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "statefulsets"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["networking.k8s.io"]
    resources  = ["ingresses", "networkpolicies"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["rbac.authorization.k8s.io"]
    resources  = ["clusterroles", "clusterrolebindings", "roles", "rolebindings"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "cloudtwin_collector" {
  count = var.collector_security_enabled ? 1 : 0

  metadata {
    name = "cloudtwin-rbac-reader"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.cloudtwin_collector[0].metadata[0].name
  }

  subject {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Group"
    name      = "cloudtwin-rbac-readers"
  }
}
