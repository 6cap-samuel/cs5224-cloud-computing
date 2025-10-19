output "http_api_url" {
  value = "${aws_apigatewayv2_api.http.api_endpoint}/${aws_apigatewayv2_stage.http.name}"
}

output "state_machine_arn" { value = aws_sfn_state_machine.pipeline.arn }
output "raw_bucket" { value = aws_s3_bucket.raw.bucket }
output "evidence_bucket" { value = aws_s3_bucket.evidence.bucket }
output "audit_bucket" { value = aws_s3_bucket.audit_log.bucket }
output "dynamodb_table" { value = aws_dynamodb_table.reports.name }
output "sns_topic_arn" { value = aws_sns_topic.officer_alerts.arn }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.officers.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.officers.id }
output "frontend_website_url" { value = aws_s3_bucket_website_configuration.image_submission_portal.website_endpoint }
output "frontend_distribution_domain" { value = aws_cloudfront_distribution.image_submission_portal.domain_name }
output "frontend_distribution_url" {
  value = "https://${aws_cloudfront_distribution.image_submission_portal.domain_name}"
}

output "officer_portal_website_url" {
  value = aws_s3_bucket_website_configuration.officer_admin_portal.website_endpoint
}

output "officer_portal_distribution_domain" {
  value = aws_cloudfront_distribution.officer_admin_portal.domain_name
}

output "officer_portal_distribution_url" {
  value = "https://${aws_cloudfront_distribution.officer_admin_portal.domain_name}"
}
