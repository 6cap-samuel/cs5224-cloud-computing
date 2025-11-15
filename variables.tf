variable "env" {
  description = "Environment (dev|staging|prod)"
  type        = string
  default     = "dev"
  validation {
    condition     = can(regex("^(dev|staging|prod)$", var.env))
    error_message = "env must be one of: dev, staging, prod."
  }
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-1"
}

variable "aws_profile" {
  description = "AWS profile for the provider (optional)"
  type        = string
  default     = null
}

variable "lambda_src_root" {
  description = "Root folder that contains Lambda source directories"
  type        = string
  default     = "./lambdas"
}

variable "lamppost_max_distance_meters" {
  description = "Maximum distance (in meters) to associate a lamppost with a submission"
  type        = number
  default     = 500
}

variable "park_max_distance_meters" {
  description = "Maximum distance (in meters) to associate a park with a submission"
  type        = number
  default     = 750
}

variable "inference_confidence_threshold" {
  description = "Default confidence threshold passed to the SageMaker inference endpoint"
  type        = number
  default     = 0.5
}

variable "sagemaker_instance_type" {
  description = "Instance type for the managed SageMaker endpoint"
  type        = string
  default     = "ml.m5.large"
}

variable "sagemaker_initial_instance_count" {
  description = "Number of instances for the SageMaker endpoint"
  type        = number
  default     = 1
}

variable "face_blur_radius" {
  description = "Gaussian blur radius used when obfuscating faces"
  type        = number
  default     = 35
}
