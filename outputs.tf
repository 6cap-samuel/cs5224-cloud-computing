output "http_api_url" {
  value = "${aws_apigatewayv2_api.http.api_endpoint}/${aws_apigatewayv2_stage.http.name}"
}

output "state_machine_arn" { value = aws_sfn_state_machine.pipeline.arn }
output "raw_bucket"        { value = aws_s3_bucket.raw.bucket }
output "evidence_bucket"   { value = aws_s3_bucket.evidence.bucket }
output "audit_bucket"      { value = aws_s3_bucket.audit_log.bucket }
output "dynamodb_table"    { value = aws_dynamodb_table.reports.name }
output "sns_topic_arn"     { value = aws_sns_topic.officer_alerts.arn }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.officers.id }
output "cognito_client_id"    { value = aws_cognito_user_pool_client.officers.id }
