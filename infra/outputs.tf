output "public_ip" {
  description = "Elastic IP address"
  value       = aws_eip.main.public_ip
}

output "domain" {
  description = "Full domain name"
  value       = "${var.subdomain}.${var.domain_name}"
}

output "https_url" {
  description = "HTTPS URL"
  value       = "https://${var.subdomain}.${var.domain_name}"
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${var.subdomain}.${var.domain_name}"
}

output "ssm_command" {
  description = "SSM Session Manager command"
  value       = "aws ssm start-session --target ${aws_instance.main.id} --region ${var.aws_region}"
}

output "webhook_base" {
  description = "Webhook base URL"
  value       = "https://${var.subdomain}.${var.domain_name}"
}

output "health_check" {
  description = "Health check URL"
  value       = "https://${var.subdomain}.${var.domain_name}/v1/health"
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.main.id
}
