variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "instance_type" {
  description = "EC2 type of the single K3s node. Only free-tier types are accepted."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = contains(["t3.micro", "t2.micro"], var.instance_type)
    error_message = "Only free-tier instance types (t3.micro, t2.micro) are allowed."
  }
}

variable "root_volume_gb" {
  description = "Size of the node's gp3 disk; the EBS free tier covers 30 GB"
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_gb >= 8 && var.root_volume_gb <= 30
    error_message = "The disk must be 8-30 GB to stay inside the EBS free tier."
  }
}

variable "k3s_version" {
  description = "K3s release, the same one the local k3d cluster runs"
  type        = string
  default     = "v1.35.5+k3s1"
}

variable "budget_email" {
  description = "Where AWS Budgets sends the cost alerts"
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_email))
    error_message = "budget_email must be an email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly spend that triggers the alerts; anything above 0 means something left the free tier"
  type        = number
  default     = 1
}
