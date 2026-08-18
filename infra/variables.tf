variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 40
}

variable "key_pair_name" {
  description = "Name of existing EC2 key pair for SSH access"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH (e.g. your IP: x.x.x.x/32)"
  type        = string
}

variable "domain_name" {
  description = "Route53 hosted zone domain"
  type        = string
  default     = "brrdcast.in"
}

variable "subdomain" {
  description = "Subdomain for the service"
  type        = string
  default     = "muldro"
}

variable "anthropic_api_key" {
  description = "Anthropic API key"
  type        = string
  sensitive   = true
}

variable "voyage_api_key" {
  description = "Voyage AI API key"
  type        = string
  sensitive   = true
}

variable "backend_token" {
  description = "Token for plugin to backend auth"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "PostgreSQL password"
  type        = string
  sensitive   = true
}

variable "github_repo_url" {
  description = "GitHub repository URL to clone"
  type        = string
}

variable "git_branch" {
  description = "Git branch to checkout"
  type        = string
  default     = "main"
}

variable "github_pat" {
  description = "GitHub Personal Access Token for cloning private repo"
  type        = string
  sensitive   = true
}

variable "project_name" {
  description = "Project name used as resource name prefix"
  type        = string
  default     = "muldro"
}
