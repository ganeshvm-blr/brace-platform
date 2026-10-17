variable "aws_region" {
  type        = string
  description = "AWS region for the public demonstration cluster."
  default     = "ap-south-1"
}

variable "cluster_name" {
  type        = string
  description = "Name of the EKS cluster for the public demonstration."
  default     = "brace-public-demo-cluster"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the demonstration VPC."
  default     = "10.0.0.0/16"
}

variable "run_id" {
  type        = string
  description = "Execution run identifier for ownership tagging."
  default     = "default"
}

variable "node_instance_type" {
  type        = string
  description = "EC2 instance type for EKS managed node group."
  default     = "t3.small"
}
