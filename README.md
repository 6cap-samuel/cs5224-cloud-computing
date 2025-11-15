# VapeWatch – CS5224 Cloud Computing Project

VapeWatch is a serverless pipeline that ingests crowd-sourced images of suspected vaping activity, enriches the reports with contextual data, runs ML inference on a managed SageMaker endpoint, and exposes the results to enforcement officers through a secured admin portal.

## Architecture at a Glance

- **Citizen submission portal:** Static S3 + CloudFront site that uploads photos and metadata to an HTTP API Gateway.
- **API Gateway → Lambda ingest:** Validates payloads, stores originals in S3, fetches weather/location context, and triggers the Step Functions pipeline.
- **State machine:** `Redaction → Inference → Enrichment → Persist`
  - Redaction stores the raw asset, invokes Rekognition to blur faces, and writes a sanitized copy.
  - Inference Lambda forwards the image to a SageMaker endpoint hosting a YOLOv8 model.
  - Enrichment (placeholder) and Persist (DynamoDB + SNS) finalize the report.
- **Officer admin portal:** Another S3 + CloudFront SPA that talks to the officer API (Cognito auth → Lambda → DynamoDB) to list and triage reports.
- **Audit trail:** DynamoDB streams → Lambda → append-only S3 bucket.

See `diagrams/aws_architecture.mmd` for the full diagram.

## Getting Started

1. **Install prerequisites**
   - Terraform ≥ 1.5, Python 3.11, AWS CLI, Node/NPM (for SPA tweaks), and Docker if you repackage models.

2. **Configure AWS credentials**
   ```bash
   export AWS_PROFILE=<your-profile>
   export AWS_REGION=ap-southeast-1
   ```

3. **Terraform deploy**
   ```bash
   terraform init
   terraform apply [-var="env=dev"]
   ```
   This provisions all infrastructure, uploads the YOLO model artifact from `scripts/sagemaker/model.tar.gz`, creates the SageMaker endpoint, and zips/deploys every Lambda.

4. **Citizen portal URL**
   - Outputs include `frontend_distribution_url`; open it and submit a test report with location metadata.

5. **Officer portal**
   - Use the `officer_portal_distribution_url` output. Default Cognito pool is empty—seed users manually or through the console.

## SageMaker Inference

Terraform uploads `scripts/sagemaker/model.tar.gz` to the managed inference bucket and creates `vapewatch-endpoint-${env}` (default `ml.m5.large`, configurable via `sagemaker_instance_type` and `sagemaker_initial_instance_count`).

Use `scripts/sagemaker/deploy_inference.py` for manual smoke tests:
```bash
python scripts/sagemaker/deploy_inference.py test --image ~/Pictures/sample.jpg
python scripts/sagemaker/deploy_inference.py list
```
Update `model.tar.gz` (keep the filename) when you retrain; a `terraform apply` will redeploy the SageMaker model + endpoint automatically.

## Redaction: Face Blurring

`lambdas/redaction` now:
- Persists the raw image to the ingestion bucket.
- Calls Amazon Rekognition (`DetectFaces`) to get bounding boxes.
- Uses Pillow to Gaussian-blur each face and stores the redacted copy in the evidence bucket.
- The blur radius is controlled by `face_blur_radius` (Terraform variable → `FACE_BLUR_RADIUS` env var).

Ensure the Lambda package contains Pillow and the execution role includes `rekognition:DetectFaces` (already configured in Terraform).

## Officer Admin Features

- Paginated list of reports with map visualization (Leaflet).
- Modal shows weather/location context, audit history, and now the inference summary (detections, confidence, endpoint).
- Officers can update audit status; changes are written back through a protected Lambda (Cognito JWT).

## Troubleshooting Tips

- **Inference fails with ValidationError:** Ensure the SageMaker endpoint is `InService` and the Lambda env `SAGEMAKER_ENDPOINT_NAME` matches. `aws sagemaker describe-endpoint --endpoint-name vapewatch-endpoint-dev`.
- **Lambda times out:** Increase `timeout` in `aws_lambda_function.inference` (default 15s) and verify the endpoint latency in CloudWatch (`/aws/sagemaker/Endpoints/<name>`).
- **No inference fields in DynamoDB:** The pipeline only writes detections when Inference + Persist run with the new code. Check CloudWatch logs for both functions.
- **Citizen portal issues:** Run `terraform output -json` to confirm the API URL and S3/CloudFront endpoints; invalid CORS headers will show up in browser devtools.

## Repository Layout

```
lambdas/            # All Lambda sources (ingest, redaction, inference, persist, etc.)
scripts/sagemaker/  # Model artifact + helper CLI for SageMaker endpoint operations
officer-admin-portal/ / image-submission-portal/  # Static site templates
diagrams/           # Architecture diagrams (Mermaid)
reports/            # Final documentation (LaTeX)
```

Each Lambda zip is created via `data "archive_file"` in `main.tf` and deployed automatically by Terraform.

## Reporting & Docs

See `reports/vapewatch_final_report.tex` and `Project Specification.pdf` for project requirements, design decisions, and future enhancements (e.g., integrating GPU inference or geo-fencing alerts). Update the LaTeX report as the architecture evolves.
