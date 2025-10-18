terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
}

resource "random_id" "suffix" {
  byte_length = 4
}

# ----------------------
# S3 buckets
# ----------------------
resource "aws_s3_bucket" "raw" {
  bucket = "vapewatch-raw-${var.env}-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_versioning" "raw_v" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "raw_pab" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "evidence" {
  bucket = "vapewatch-evidence-${var.env}-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_versioning" "evidence_v" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "evidence_pab" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Immutable audit log with S3 Object Lock (WORM)
resource "aws_s3_bucket" "audit_log" {
  bucket              = "vapewatch-audit-${var.env}-${random_id.suffix.hex}"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "audit_v" {
  bucket = aws_s3_bucket.audit_log.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "audit_lock" {
  bucket              = aws_s3_bucket.audit_log.id
  object_lock_enabled = "Enabled"

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 90
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit_pab" {
  bucket                  = aws_s3_bucket.audit_log.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ----------------------
# DynamoDB (Reports)
# ----------------------
resource "aws_dynamodb_table" "reports" {
  name         = "vapewatch-reports-${var.env}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "report_id"
  range_key = "submitted_at"

  attribute {
    name = "report_id"
    type = "S"
  }

  attribute {
    name = "submitted_at"
    type = "S"
  }

  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"
}

# ----------------------
# SNS (Officer alerts) - no tags
# ----------------------
resource "aws_sns_topic" "officer_alerts" {
  name = "vapewatch-officer-alerts-${random_id.suffix.hex}"
}

# ----------------------
# Cognito (minimal scaffold)
# ----------------------
resource "aws_cognito_user_pool" "officers" {
  name = "vapewatch-officers-${var.env}"
}

resource "aws_cognito_user_pool_client" "officers" {
  name           = "vapewatch-officers-client-${var.env}"
  user_pool_id   = aws_cognito_user_pool.officers.id
  generate_secret = false
}

# ----------------------
# IAM for Lambdas & Step Functions
# ----------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = "vapewatch-lambda-exec-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_inline" {
  statement {
    sid     = "S3Access"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.raw.arn,
      "${aws_s3_bucket.raw.arn}/*",
      aws_s3_bucket.evidence.arn,
      "${aws_s3_bucket.evidence.arn}/*",
      aws_s3_bucket.audit_log.arn,
      "${aws_s3_bucket.audit_log.arn}/*"
    ]
  }

  statement {
    sid     = "DynamoDBAccess"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
      "dynamodb:DescribeStream",
      "dynamodb:GetRecords",
      "dynamodb:GetShardIterator",
      "dynamodb:ListStreams"
    ]
    resources = [
      aws_dynamodb_table.reports.arn,
      aws_dynamodb_table.reports.stream_arn
    ]
  }

  statement {
    sid       = "SNSPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.officer_alerts.arn]
  }

  statement {
    sid       = "StartStateMachine"
    actions   = ["states:StartExecution"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "lambda_inline" {
  name   = "vapewatch-lambda-inline-${var.env}"
  policy = data.aws_iam_policy_document.lambda_inline.json
}

resource "aws_iam_role_policy_attachment" "lambda_inline_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_inline.arn
}

# Step Functions role
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn_exec" {
  name               = "vapewatch-sfn-exec-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn_inline" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = ["*"]
  }

  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.officer_alerts.arn]
  }
}

resource "aws_iam_policy" "sfn_inline" {
  name   = "vapewatch-sfn-inline-${var.env}"
  policy = data.aws_iam_policy_document.sfn_inline.json
}

resource "aws_iam_role_policy_attachment" "sfn_inline_attach" {
  role       = aws_iam_role.sfn_exec.name
  policy_arn = aws_iam_policy.sfn_inline.arn
}

# ----------------------
# Package Lambdas (zip)
# ----------------------
data "archive_file" "ingest_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/ingest"
  output_path = "${path.module}/artifacts/ingest.zip"
}

data "archive_file" "redaction_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/redaction"
  output_path = "${path.module}/artifacts/redaction.zip"
}

data "archive_file" "inference_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/inference"
  output_path = "${path.module}/artifacts/inference.zip"
}

data "archive_file" "enrichment_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/enrichment"
  output_path = "${path.module}/artifacts/enrichment.zip"
}

data "archive_file" "persist_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/persist"
  output_path = "${path.module}/artifacts/persist.zip"
}

data "archive_file" "audit_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/audit_sink"
  output_path = "${path.module}/artifacts/audit_sink.zip"
}

# ----------------------
# Lambda functions
# ----------------------
resource "aws_lambda_function" "ingest" {
  function_name = "vapewatch-ingest-${var.env}"
  role          = aws_iam_role.lambda_exec.arn
  filename      = data.archive_file.ingest_zip.output_path
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
    }
  }
}

resource "aws_lambda_function" "redaction" {
  function_name = "vapewatch-redaction-${var.env}"
  role          = aws_iam_role.lambda_exec.arn
  filename      = data.archive_file.redaction_zip.output_path
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  environment {
    variables = {
      RAW_BUCKET      = aws_s3_bucket.raw.bucket
      EVIDENCE_BUCKET = aws_s3_bucket.evidence.bucket
    }
  }
}

resource "aws_lambda_function" "inference" {
  function_name = "vapewatch-inference-${var.env}"
  role          = aws_iam_role.lambda_exec.arn
  filename      = data.archive_file.inference_zip.output_path
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
}

resource "aws_lambda_function" "enrichment" {
  function_name = "vapewatch-enrichment-${var.env}"
  role          = aws_iam_role.lambda_exec.arn
  filename      = data.archive_file.enrichment_zip.output_path
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
}

resource "aws_lambda_function" "persist" {
  function_name = "vapewatch-persist-${var.env}"
  role          = aws_iam_role.lambda_exec.arn
  filename      = data.archive_file.persist_zip.output_path
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  environment {
    variables = {
      REPORTS_TABLE = aws_dynamodb_table.reports.name
      ALERTS_TOPIC  = aws_sns_topic.officer_alerts.arn
    }
  }
}

# Audit sink: DDB stream -> append-only S3 WORM (hash chain done in code)
resource "aws_lambda_function" "audit_sink" {
  function_name = "vapewatch-audit-sink-${var.env}"
  role          = aws_iam_role.lambda_exec.arn
  filename      = data.archive_file.audit_zip.output_path
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  environment {
    variables = {
      AUDIT_BUCKET = aws_s3_bucket.audit_log.bucket
    }
  }
}

resource "aws_lambda_event_source_mapping" "reports_stream" {
  event_source_arn  = aws_dynamodb_table.reports.stream_arn
  function_name     = aws_lambda_function.audit_sink.arn
  starting_position = "LATEST"
}

# ----------------------
# Step Functions pipeline
# ----------------------
locals {
  sfn_def = jsonencode({
    Comment = "VapeWatch pipeline",
    StartAt = "Redaction",
    States  = {
      Redaction = {
        Type     = "Task",
        Resource = "${aws_lambda_function.redaction.arn}",
        Next     = "Inference"
      },
      Inference = {
        Type     = "Task",
        Resource = "${aws_lambda_function.inference.arn}",
        Next     = "Enrichment"
      },
      Enrichment = {
        Type     = "Task",
        Resource = "${aws_lambda_function.enrichment.arn}",
        Next     = "Persist"
      },
      Persist = {
        Type     = "Task",
        Resource = "${aws_lambda_function.persist.arn}",
        End      = true
      }
    }
  })
}

resource "aws_sfn_state_machine" "pipeline" {
  name       = "vapewatch-pipeline-${var.env}"
  role_arn   = aws_iam_role.sfn_exec.arn
  definition = local.sfn_def
}

# ----------------------
# API Gateway HTTP API -> Ingest Lambda
# ----------------------
resource "aws_apigatewayv2_api" "http" {
  name          = "vapewatch-api-${var.env}"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "ingest" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingest.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "reports" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /reports"
  target    = "integrations/${aws_apigatewayv2_integration.ingest.id}"
}

resource "aws_lambda_permission" "api_invoke_ingest" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_apigatewayv2_stage" "http" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = var.env
  auto_deploy = true
}
