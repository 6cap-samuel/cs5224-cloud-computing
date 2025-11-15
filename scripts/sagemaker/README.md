## VapeWatch SageMaker Toolkit

This folder contains the files that are required to publish the YOLOv8 model to SageMaker:

| File | Description |
| --- | --- |
| `deploy_inference.py` | Helper script for manual operations (test, list, delete) against the managed SageMaker endpoint. Terraform now creates the model and endpoint automatically. |
| `model.tar.gz` | Packaged weights and inference dependencies for YOLOv8. Terraform uploads this file to the inference artifact bucket during `terraform apply`. Update this file and re-run `terraform apply` to roll out a new model. |
| `inference_model/` | Entry-point script (`inference.py`) and Python dependencies that SageMaker loads next to the weights (already included inside `model.tar.gz`). |
| `config.example.json` | Template for deployment configuration (region, role ARN, endpoint name, etc.). Copy this to `deploy_config.json` and fill in your environment-specific values; the file is git-ignored. |
| `test_vapewatch_api.ps1` | Quick PowerShell helper to send a sample image through the HTTP API (Step Functions + Lambda pipeline). |

### Typical workflow

1. Provision infrastructure with Terraform. The apply step now uploads `scripts/sagemaker/model.tar.gz`, creates the SageMaker model + endpoint configuration, and ensures the managed endpoint (`vapewatch-endpoint-${var.env}`) exists.
2. Whenever you train a new model, update `scripts/sagemaker/model.tar.gz` (keeping the same filename) and rerun `terraform apply`. The `source_code_hash`/ETag wiring forces Terraform to re-upload the artifact and replace the model + endpoint.
3. Validate the endpoint with a local file:
   ```bash
   python scripts/sagemaker/deploy_inference.py test --image ~/Pictures/sample.jpg
   ```
4. Use the other subcommands (`list`, `delete`) for manual inspection or emergency teardown if needed. Terraform will recreate the endpoint on the next apply if you delete it manually.

The inference Lambda reads the endpoint name from its environment variables and streams every redacted image through the managed SageMaker endpoint before persisting the final report.
