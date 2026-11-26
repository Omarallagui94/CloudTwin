output "configure_kubectl" {
  description = "Command to update kubeconfig for this cluster"
  value       = module.retail_app_eks.configure_kubectl
}

output "cloudtwin_collector_role_arn" {
  description = "ARN of the optional read-only CloudTwin collector role"
  value       = var.collector_security_enabled ? aws_iam_role.cloudtwin_collector[0].arn : null
}
