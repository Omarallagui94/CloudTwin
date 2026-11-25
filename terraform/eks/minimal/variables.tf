variable "environment_name" {
  description = "Name of the environment"
  type        = string
  default     = "retail-store"
}

variable "istio_enabled" {
  description = "Boolean value that enables istio."
  type        = bool
  default     = false
}

variable "opentelemetry_enabled" {
  description = "Boolean value that enables OpenTelemetry."
  type        = bool
  default     = false
}

variable "collector_security_enabled" {
  description = "Create the CloudTwin read-only collector role, aws-auth mapping, and Kubernetes RBAC"
  type        = bool
  default     = false
}

variable "collector_trusted_principal_arns" {
  description = "IAM principal ARNs allowed to assume the CloudTwin collector role"
  type        = list(string)
  default     = []

  validation {
    condition     = !var.collector_security_enabled || length(var.collector_trusted_principal_arns) > 0
    error_message = "collector_trusted_principal_arns must be non-empty when collector security is enabled."
  }
}
