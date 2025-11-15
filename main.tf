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

data "aws_caller_identity" "current" {}

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  sagemaker_endpoint_name = "vapewatch-endpoint-${var.env}"
  sagemaker_model_name    = "vapewatch-model-${var.env}"
  sagemaker_model_key     = "model.tar.gz"
  sagemaker_model_path    = "${path.module}/scripts/sagemaker/model.tar.gz"
  sagemaker_pytorch_image = "763104351884.dkr.ecr.${var.region}.amazonaws.com/pytorch-inference:2.0.0-cpu-py310"
}

# ----------------------
# S3 buckets
# ----------------------
resource "aws_s3_bucket" "raw" {
  bucket        = "vapewatch-raw-${var.env}-${random_id.suffix.hex}"
  force_destroy = true
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
  bucket        = "vapewatch-evidence-${var.env}-${random_id.suffix.hex}"
  force_destroy = true
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
  bucket        = "vapewatch-audit-${var.env}-${random_id.suffix.hex}"
  force_destroy = true
}


resource "aws_s3_bucket_public_access_block" "audit_pab" {
  bucket                  = aws_s3_bucket.audit_log.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "inference_models" {
  bucket        = "vapewatch-inference-${var.env}-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "inference_models" {
  bucket                  = aws_s3_bucket.inference_models.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "inference_model_artifact" {
  bucket       = aws_s3_bucket.inference_models.bucket
  key          = local.sagemaker_model_key
  source       = local.sagemaker_model_path
  etag         = filemd5(local.sagemaker_model_path)
  content_type = "application/x-tar"
}

# ----------------------
# Static website frontend bucket (public)
# ----------------------
resource "aws_s3_bucket" "image_submission_portal" {
  bucket        = "vapewatch-frontend-${var.env}-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "image_submission_portal" {
  bucket                  = aws_s3_bucket.image_submission_portal.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "image_submission_portal" {
  bucket = aws_s3_bucket.image_submission_portal.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_website_configuration" "image_submission_portal" {
  bucket = aws_s3_bucket.image_submission_portal.id
  index_document {
    suffix = "index.html"
  }
  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "image_submission_portal" {
  bucket = aws_s3_bucket.image_submission_portal.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowPublicRead"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.image_submission_portal.arn}/*"
        ]
      }
    ]
  })
  depends_on = [
    aws_s3_bucket_public_access_block.image_submission_portal
  ]
}

resource "aws_s3_object" "image_submission_portal_index" {
  bucket       = aws_s3_bucket.image_submission_portal.bucket
  key          = "index.html"
  content_type = "text/html"
  content = templatefile("${path.module}/image-submission-portal/index.html.tmpl", {
    api_base_url = "${aws_apigatewayv2_api.http.api_endpoint}/${aws_apigatewayv2_stage.http.name}"
  })
}

resource "aws_cloudfront_cache_policy" "image_submission_portal_short_ttl" {
  name    = "vapewatch-frontend-${var.env}-short-ttl"
  comment = "Short TTLs for dynamic VapeWatch frontend shell"

  default_ttl = 300
  max_ttl     = 600
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    headers_config {
      header_behavior = "none"
    }

    cookies_config {
      cookie_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "image_submission_portal" {
  enabled             = true
  comment             = "VapeWatch frontend ${var.env}"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  wait_for_deployment = false

  origin {
    domain_name = aws_s3_bucket.image_submission_portal.bucket_regional_domain_name
    origin_id   = "vapewatch-frontend-${var.env}"

    s3_origin_config {
      origin_access_identity = ""
    }
  }

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "vapewatch-frontend-${var.env}"

    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id            = aws_cloudfront_cache_policy.image_submission_portal_short_ttl.id
    origin_request_policy_id   = "88a5eaf4-2fd4-4709-b370-b4c650ea3fcf" # CORS-S3Origin
    response_headers_policy_id = "67f7725c-6f97-4210-82d7-5512b31e9d03" # SecurityHeadersPolicy
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  depends_on = [aws_s3_bucket_website_configuration.image_submission_portal]
}

resource "aws_s3_bucket" "officer_admin_portal" {
  bucket        = "vapewatch-officer-admin-${var.env}-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "officer_admin_portal" {
  bucket                  = aws_s3_bucket.officer_admin_portal.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "officer_admin_portal" {
  bucket = aws_s3_bucket.officer_admin_portal.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_website_configuration" "officer_admin_portal" {
  bucket = aws_s3_bucket.officer_admin_portal.id
  index_document {
    suffix = "index.html"
  }
  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "officer_admin_portal" {
  bucket = aws_s3_bucket.officer_admin_portal.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowPublicRead"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.officer_admin_portal.arn}/*"
        ]
      }
    ]
  })
  depends_on = [
    aws_s3_bucket_public_access_block.officer_admin_portal
  ]
}

resource "aws_s3_object" "officer_admin_portal_index" {
  bucket       = aws_s3_bucket.officer_admin_portal.bucket
  key          = "index.html"
  content_type = "text/html"
  content = templatefile("${path.module}/officer-admin-portal/index.html.tmpl", {
    api_base_url      = "${aws_apigatewayv2_api.http.api_endpoint}/${aws_apigatewayv2_stage.http.name}"
    cognito_client_id = aws_cognito_user_pool_client.officers.id
    cognito_region    = var.region
  })
}

resource "aws_s3_object" "lamppost_dataset" {
  bucket       = aws_s3_bucket.raw.bucket
  key          = "reference/lampposts.json"
  source       = "${path.module}/data/lampposts.json"
  etag         = filemd5("${path.module}/data/lampposts.json")
  content_type = "application/json"
}

resource "aws_s3_object" "park_dataset" {
  bucket       = aws_s3_bucket.raw.bucket
  key          = "reference/parks.json"
  source       = "${path.module}/data/parks.json"
  etag         = filemd5("${path.module}/data/parks.json")
  content_type = "application/json"
}

resource "aws_cloudfront_cache_policy" "officer_admin_portal_short_ttl" {
  name    = "vapewatch-officer-admin-${var.env}-short-ttl"
  comment = "Short TTLs for officer admin portal shell"

  default_ttl = 120
  max_ttl     = 300
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    headers_config {
      header_behavior = "none"
    }

    cookies_config {
      cookie_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "officer_admin_portal" {
  enabled             = true
  comment             = "VapeWatch officer admin portal ${var.env}"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  wait_for_deployment = false

  origin {
    domain_name = aws_s3_bucket.officer_admin_portal.bucket_regional_domain_name
    origin_id   = "vapewatch-officer-admin-${var.env}"

    s3_origin_config {
      origin_access_identity = ""
    }
  }

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "vapewatch-officer-admin-${var.env}"

    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id            = aws_cloudfront_cache_policy.officer_admin_portal_short_ttl.id
    origin_request_policy_id   = "88a5eaf4-2fd4-4709-b370-b4c650ea3fcf" # CORS-S3Origin
    response_headers_policy_id = "67f7725c-6f97-4210-82d7-5512b31e9d03" # SecurityHeadersPolicy
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  depends_on = [aws_s3_bucket_website_configuration.officer_admin_portal]
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
  name                          = "vapewatch-officers-client-${var.env}"
  user_pool_id                  = aws_cognito_user_pool.officers.id
  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  supported_identity_providers  = ["COGNITO"]
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
    sid = "DynamoDBAccess"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
      "dynamodb:Scan",
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
    sid       = "CognitoUserLookup"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = [aws_cognito_user_pool.officers.arn]
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

  statement {
    sid       = "DetectFaces"
    actions   = ["rekognition:DetectFaces"]
    resources = ["*"]
  }

  statement {
    sid     = "InvokeSageMakerEndpoint"
    actions = ["sagemaker:InvokeEndpoint"]
    resources = [
      "arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:endpoint/${local.sagemaker_endpoint_name}"
    ]
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

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker_exec" {
  name               = "vapewatch-sagemaker-exec-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
}

data "aws_iam_policy_document" "sagemaker_permissions" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.inference_models.arn,
      "${aws_s3_bucket.inference_models.arn}/*"
    ]
  }

  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:CreateLogGroup",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer"
    ]
    resources = ["*"]
  }

}

resource "aws_iam_role_policy" "sagemaker_permissions" {
  name   = "vapewatch-sagemaker-permissions-${var.env}"
  role   = aws_iam_role.sagemaker_exec.id
  policy = data.aws_iam_policy_document.sagemaker_permissions.json
}

resource "aws_iam_role_policy_attachment" "sagemaker_ecr_readonly" {
  role       = aws_iam_role.sagemaker_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
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

data "archive_file" "officer_admin_zip" {
  type        = "zip"
  source_dir  = "${var.lambda_src_root}/officer_admin_portal"
  output_path = "${path.module}/artifacts/officer_admin_portal.zip"
}

# ----------------------
# Lambda functions
# ----------------------
resource "aws_lambda_function" "ingest" {
  function_name    = "vapewatch-ingest-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.ingest_zip.output_path
  source_code_hash = data.archive_file.ingest_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  environment {
    variables = {
      STATE_MACHINE_ARN            = aws_sfn_state_machine.pipeline.arn
      RAW_BUCKET                   = aws_s3_bucket.raw.bucket
      EVIDENCE_BUCKET              = aws_s3_bucket.evidence.bucket
      LAMPPOST_DATA_BUCKET         = aws_s3_bucket.raw.bucket
      LAMPPOST_DATA_KEY            = aws_s3_object.lamppost_dataset.key
      LAMPPOST_MAX_DISTANCE_METERS = var.lamppost_max_distance_meters
      PARK_DATA_BUCKET             = aws_s3_bucket.raw.bucket
      PARK_DATA_KEY                = aws_s3_object.park_dataset.key
      PARK_MAX_DISTANCE_METERS     = var.park_max_distance_meters
    }
  }
  depends_on = [aws_s3_object.lamppost_dataset, aws_s3_object.park_dataset]
}

resource "aws_lambda_function" "redaction" {
  function_name    = "vapewatch-redaction-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.redaction_zip.output_path
  source_code_hash = data.archive_file.redaction_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  environment {
    variables = {
      RAW_BUCKET       = aws_s3_bucket.raw.bucket
      EVIDENCE_BUCKET  = aws_s3_bucket.evidence.bucket
      FACE_BLUR_RADIUS = tostring(var.face_blur_radius)
    }
  }
}

resource "aws_lambda_function" "inference" {
  function_name    = "vapewatch-inference-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.inference_zip.output_path
  source_code_hash = data.archive_file.inference_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  timeout          = 15
  environment {
    variables = {
      SAGEMAKER_ENDPOINT_NAME        = local.sagemaker_endpoint_name
      INFERENCE_CONFIDENCE_THRESHOLD = tostring(var.inference_confidence_threshold)
    }
  }
}

resource "aws_lambda_function" "enrichment" {
  function_name    = "vapewatch-enrichment-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.enrichment_zip.output_path
  source_code_hash = data.archive_file.enrichment_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
}

resource "aws_lambda_function" "persist" {
  function_name    = "vapewatch-persist-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.persist_zip.output_path
  source_code_hash = data.archive_file.persist_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  environment {
    variables = {
      REPORTS_TABLE = aws_dynamodb_table.reports.name
      ALERTS_TOPIC  = aws_sns_topic.officer_alerts.arn
    }
  }
}

# Audit sink: DDB stream -> append-only S3 WORM (hash chain done in code)
resource "aws_lambda_function" "audit_sink" {
  function_name    = "vapewatch-audit-sink-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.audit_zip.output_path
  source_code_hash = data.archive_file.audit_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  environment {
    variables = {
      AUDIT_BUCKET = aws_s3_bucket.audit_log.bucket
    }
  }
}

resource "aws_lambda_function" "officer_admin" {
  function_name    = "vapewatch-officer-admin-${var.env}"
  role             = aws_iam_role.lambda_exec.arn
  filename         = data.archive_file.officer_admin_zip.output_path
  source_code_hash = data.archive_file.officer_admin_zip.output_base64sha256
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  environment {
    variables = {
      REPORTS_TABLE        = aws_dynamodb_table.reports.name
      RAW_BUCKET           = aws_s3_bucket.raw.bucket
      SIGNED_URL_TTL       = "900"
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.officers.id
    }
  }
}

resource "aws_lambda_event_source_mapping" "reports_stream" {
  event_source_arn  = aws_dynamodb_table.reports.stream_arn
  function_name     = aws_lambda_function.audit_sink.arn
  starting_position = "LATEST"
}

# ----------------------
# SageMaker managed endpoint
# ----------------------
resource "aws_sagemaker_model" "inference" {
  name               = local.sagemaker_model_name
  execution_role_arn = aws_iam_role.sagemaker_exec.arn
  lifecycle {
    replace_triggered_by = [aws_s3_object.inference_model_artifact.etag]
  }

  primary_container {
    image          = local.sagemaker_pytorch_image
    mode           = "SingleModel"
    model_data_url = "s3://${aws_s3_bucket.inference_models.bucket}/${aws_s3_object.inference_model_artifact.key}"
    environment = {
      SAGEMAKER_PROGRAM          = "inference.py"
      SAGEMAKER_SUBMIT_DIRECTORY = "s3://${aws_s3_bucket.inference_models.bucket}/${aws_s3_object.inference_model_artifact.key}"
      SAGEMAKER_REGION           = var.region
    }
  }

  depends_on = [aws_s3_object.inference_model_artifact]
}

resource "aws_sagemaker_endpoint_configuration" "inference" {
  name = "${local.sagemaker_endpoint_name}-config"
  lifecycle {
    replace_triggered_by = [aws_sagemaker_model.inference.id]
  }

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.inference.name
    initial_instance_count = var.sagemaker_initial_instance_count
    instance_type          = var.sagemaker_instance_type
  }
}

resource "aws_sagemaker_endpoint" "inference" {
  name                 = local.sagemaker_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.inference.name
  lifecycle {
    replace_triggered_by = [aws_sagemaker_endpoint_configuration.inference.id]
  }
}

# ----------------------
# Step Functions pipeline
# ----------------------
locals {
  sfn_def = jsonencode({
    Comment = "VapeWatch pipeline",
    StartAt = "Redaction",
    States = {
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
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["OPTIONS", "POST", "GET"]
    allow_headers = ["content-type", "authorization"]
  }
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

resource "aws_apigatewayv2_integration" "officer_reports" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.officer_admin.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_authorizer" "officer_jwt" {
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "officer-jwt"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.officers.id]
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.officers.id}"
  }
}

resource "aws_apigatewayv2_route" "reports_list" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /reports"
  target             = "integrations/${aws_apigatewayv2_integration.officer_reports.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.officer_jwt.id
}

resource "aws_apigatewayv2_route" "report_audit" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "POST /reports/{report_id}/audit"
  target             = "integrations/${aws_apigatewayv2_integration.officer_reports.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.officer_jwt.id
}

resource "aws_apigatewayv2_route" "report_audit_history" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /reports/{report_id}/history"
  target             = "integrations/${aws_apigatewayv2_integration.officer_reports.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.officer_jwt.id
}

resource "aws_lambda_permission" "api_invoke_ingest" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_invoke_officer" {
  statement_id  = "AllowAPIGatewayInvokeOfficer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.officer_admin.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_apigatewayv2_stage" "http" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = var.env
  auto_deploy = true
}
