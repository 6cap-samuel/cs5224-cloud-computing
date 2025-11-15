# cs5224-cloud-computing

## SageMaker deployment

Terraform now provisions the full SageMaker stack (model artifact upload, execution role, endpoint configuration, and endpoint) referenced by the inference Lambda. See `scripts/sagemaker/README.md` if you need to rebuild the model artifact or run manual smoke tests against the endpoint.
