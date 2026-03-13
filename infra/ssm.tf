resource "aws_ssm_parameter" "anthropic_api_key" {
  name  = "/${var.project_name}/anthropic-api-key"
  type  = "SecureString"
  value = var.anthropic_api_key

  tags = {
    Name = "${var.project_name}-anthropic-api-key"
  }
}

resource "aws_ssm_parameter" "voyage_api_key" {
  name  = "/${var.project_name}/voyage-api-key"
  type  = "SecureString"
  value = var.voyage_api_key

  tags = {
    Name = "${var.project_name}-voyage-api-key"
  }
}

resource "aws_ssm_parameter" "backend_token" {
  name  = "/${var.project_name}/backend-token"
  type  = "SecureString"
  value = var.backend_token

  tags = {
    Name = "${var.project_name}-backend-token"
  }
}

resource "aws_ssm_parameter" "postgres_password" {
  name  = "/${var.project_name}/postgres-password"
  type  = "SecureString"
  value = var.postgres_password

  tags = {
    Name = "${var.project_name}-postgres-password"
  }
}

resource "aws_ssm_parameter" "openclaw_gateway_token" {
  name  = "/${var.project_name}/openclaw-gateway-token"
  type  = "SecureString"
  value = var.openclaw_gateway_token

  tags = {
    Name = "${var.project_name}-openclaw-gateway-token"
  }
}

resource "aws_ssm_parameter" "openclaw_hook_token" {
  name  = "/${var.project_name}/openclaw-hook-token"
  type  = "SecureString"
  value = var.openclaw_hook_token

  tags = {
    Name = "${var.project_name}-openclaw-hook-token"
  }
}

resource "aws_ssm_parameter" "github_pat" {
  name  = "/${var.project_name}/github-pat"
  type  = "SecureString"
  value = var.github_pat

  tags = {
    Name = "${var.project_name}-github-pat"
  }
}
