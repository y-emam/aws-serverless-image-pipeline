variable "aws_region" {
  description = "Region for all resources. us-east-1 has the widest Bedrock model coverage."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use. null = default credential chain."
  type        = string
  default     = null
}

variable "project_name" {
  description = "Name prefix and value of the Project tag."
  type        = string
  default     = "manara-image-pipeline"
}

variable "alert_email" {
  description = "Email for SNS notifications and the budget alert. Keep it in terraform.tfvars (gitignored)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@ ]+@[^@ ]+[.][^@ ]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "budget_limit_usd" {
  description = "Monthly AWS Budgets limit in USD."
  type        = number
  default     = 5
}

variable "raw_retention_days" {
  description = "Days before raw uploads in the source bucket are deleted."
  type        = number
  default     = 30

  validation {
    condition     = var.raw_retention_days >= 1
    error_message = "raw_retention_days must be at least 1."
  }
}

variable "processed_ia_after_days" {
  description = "Days before processed images move to S3 Standard-IA (minimum 30)."
  type        = number
  default     = 30

  validation {
    condition     = var.processed_ia_after_days >= 30
    error_message = "S3 requires objects to be at least 30 days old before a transition to STANDARD_IA."
  }
}

variable "max_upload_mb" {
  description = "Largest upload accepted, in MB. Enforced by the pre-signed POST policy and again in the validate step."
  type        = number
  default     = 10

  validation {
    condition     = var.max_upload_mb >= 1 && var.max_upload_mb <= 50
    error_message = "max_upload_mb must be between 1 and 50."
  }
}

variable "thumbnail_px" {
  description = "Longest edge of the thumbnail, in pixels."
  type        = number
  default     = 320
}

variable "full_px" {
  description = "Longest edge of the full-size web image, in pixels."
  type        = number
  default     = 1600
}

variable "watermark_text" {
  description = "Text stamped on the full-size image."
  type        = string
  default     = "manara-image-pipeline"
}

variable "enable_bedrock" {
  description = "Generate a caption with Bedrock. Set false if model access is not enabled on the account."
  type        = bool
  default     = true
}

variable "bedrock_model_id" {
  description = "Bedrock inference profile id used for captions (Converse API). Haiku 5.5 is only offered through cross-region profiles in us-east-1."
  type        = string
  default     = "us.anthropic.claude-haiku-5-5"
}

variable "lambda_architecture" {
  description = "Lambda CPU architecture. arm64 (Graviton) is about 20% cheaper per GB-second."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture must be arm64 or x86_64."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for Lambda and Step Functions logs."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a value CloudWatch Logs accepts (e.g. 7, 14, 30)."
  }
}

variable "extra_cors_origins" {
  description = "Extra browser origins allowed to upload directly to S3 and call the API (e.g. http://localhost:8000 for local frontend work)."
  type        = list(string)
  default     = []
}

variable "api_throttle_rate" {
  description = "Steady-state requests per second allowed on the public API."
  type        = number
  default     = 5

  validation {
    condition     = var.api_throttle_rate > 0
    error_message = "api_throttle_rate must be greater than 0."
  }
}

variable "api_throttle_burst" {
  description = "Burst requests allowed on the public API."
  type        = number
  default     = 10

  validation {
    condition     = var.api_throttle_burst > 0
    error_message = "api_throttle_burst must be greater than 0."
  }
}
