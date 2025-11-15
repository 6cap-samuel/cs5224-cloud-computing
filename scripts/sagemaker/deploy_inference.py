#!/usr/bin/env python3
"""Deploy VapeWatch's YOLOv8 model to SageMaker."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
import sagemaker
from botocore.exceptions import ClientError
from sagemaker.pytorch import PyTorchModel

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "deploy_config.json"
DEFAULT_SOURCE_DIR = ROOT / "inference_model"
DEFAULT_ARTIFACT = ROOT / "model.tar.gz"
DEFAULT_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-southeast-1"

LOG_FORMAT = "%(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("vapewatch.sagemaker")


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - configuration error.
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def _resolve_region(raw: Optional[str], config: Dict[str, Any]) -> str:
    return raw or config.get("region") or DEFAULT_REGION


def _ensure_model_artifact(settings: Dict[str, Any], session: boto3.session.Session) -> str:
    model_data = settings.get("model_data")
    if isinstance(model_data, str) and model_data.startswith("s3://"):
        return model_data

    artifact_path = Path(settings.get("artifact_path") or DEFAULT_ARTIFACT)
    if not artifact_path.exists():
        raise SystemExit(f"Artifact not found: {artifact_path}")

    bucket = settings.get("artifact_bucket")
    if not bucket:
        raise SystemExit("artifact_bucket is required when uploading a local artifact")

    prefix = (settings.get("artifact_prefix") or "").strip("/")
    key_parts = [part for part in (prefix, artifact_path.name) if part]
    key = "/".join(key_parts)
    s3_client = session.client("s3")
    log.info("Uploading %s to s3://%s/%s", artifact_path, bucket, key)
    try:
        s3_client.upload_file(str(artifact_path), bucket, key)
    except ClientError as exc:
        raise SystemExit(f"Failed to upload artifact: {exc}") from exc
    return f"s3://{bucket}/{key}"


def _ensure_source_dir(path: Optional[str]) -> Path:
    src = Path(path or DEFAULT_SOURCE_DIR)
    if not (src.exists() and src.is_dir()):
        raise SystemExit(f"Source directory not found: {src}")
    return src


def _build_session(region: str) -> sagemaker.session.Session:
    boto_session = boto3.session.Session(region_name=region)
    return sagemaker.session.Session(boto_session=boto_session)


def deploy_endpoint(args: argparse.Namespace, config: Dict[str, Any]) -> None:
    region = _resolve_region(args.region, config)
    settings = {
        "role_arn": args.role_arn or config.get("role_arn"),
        "endpoint_name": args.endpoint_name or config.get("endpoint_name") or f"vapewatch-endpoint-{region}",
        "model_name": args.model_name or config.get("model_name") or f"vapewatch-model-{region}",
        "model_data": args.model_data or config.get("model_data"),
        "artifact_bucket": args.artifact_bucket or config.get("artifact_bucket"),
        "artifact_prefix": args.artifact_prefix or config.get("artifact_prefix"),
        "artifact_path": args.artifact_path or config.get("artifact_path"),
        "instance_type": args.instance_type or config.get("instance_type") or "ml.m5.large",
        "instance_count": args.instance_count or config.get("instance_count") or 1,
        "wait": not args.no_wait,
        "source_dir": args.source_dir or config.get("source_dir"),
        "entry_point": args.entry_point or config.get("entry_point") or "inference.py",
        "framework_version": args.framework_version or config.get("framework_version") or "2.0.0",
        "py_version": args.py_version or config.get("py_version") or "py310",
    }
    role_arn = settings["role_arn"]
    if not role_arn:
        raise SystemExit("role_arn is required for deployment")

    session = _build_session(region)
    model_data = _ensure_model_artifact(settings, session.boto_session)
    source_dir = _ensure_source_dir(settings["source_dir"])

    log.info("Deploying model %s to endpoint %s in %s", settings["model_name"], settings["endpoint_name"], region)
    pytorch_model = PyTorchModel(
        model_data=model_data,
        role=role_arn,
        entry_point=settings["entry_point"],
        source_dir=str(source_dir),
        framework_version=settings["framework_version"],
        py_version=settings["py_version"],
        sagemaker_session=session,
    )

    predictor = pytorch_model.deploy(
        endpoint_name=settings["endpoint_name"],
        model_name=settings["model_name"],
        initial_instance_count=settings["instance_count"],
        instance_type=settings["instance_type"],
        wait=settings["wait"],
    )
    log.info("Deployment started for endpoint %s", predictor.endpoint_name)


def test_endpoint(args: argparse.Namespace, config: Dict[str, Any]) -> None:
    region = _resolve_region(args.region, config)
    endpoint_name = args.endpoint_name or config.get("endpoint_name")
    if not endpoint_name:
        raise SystemExit("endpoint_name is required to test the endpoint")

    image_path = Path(args.image or config.get("test_image", ""))
    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")

    threshold = float(args.confidence or config.get("confidence", 0.5))
    payload = {
        "image": base64.b64encode(image_path.read_bytes()).decode("utf-8"),
        "confidence_threshold": threshold,
    }

    runtime = boto3.client("sagemaker-runtime", region_name=region)
    log.info("Invoking %s with %s", endpoint_name, image_path)
    response = runtime.invoke_endpoint(
        EndpointName=endpoint_name,
        ContentType="application/json",
        Accept="application/json",
        Body=json.dumps(payload),
    )
    result = json.loads(response["Body"].read().decode("utf-8"))
    print(json.dumps(result, indent=2))


def list_endpoints(args: argparse.Namespace, config: Dict[str, Any]) -> None:
    region = _resolve_region(args.region, config)
    client = boto3.client("sagemaker", region_name=region)
    filters: Dict[str, Any] = {}
    if args.name_contains:
        filters["NameContains"] = args.name_contains
    resp = client.list_endpoints(**filters)
    endpoints = resp.get("Endpoints", [])
    if not endpoints:
        print("No endpoints found.")
        return
    for entry in endpoints:
        print(f"{entry['EndpointName']}  {entry['EndpointStatus']}")


def delete_endpoint(args: argparse.Namespace, config: Dict[str, Any]) -> None:
    region = _resolve_region(args.region, config)
    endpoint_name = args.endpoint_name or config.get("endpoint_name")
    if not endpoint_name:
        raise SystemExit("endpoint_name is required to delete an endpoint")

    client = boto3.client("sagemaker", region_name=region)
    log.info("Deleting endpoint %s", endpoint_name)
    client.delete_endpoint(EndpointName=endpoint_name)

    if args.delete_config or args.delete_model:
        config_name = args.endpoint_config or config.get("endpoint_config") or endpoint_name
        model_name = args.model_name or config.get("model_name")
        if args.delete_config:
            try:
                client.delete_endpoint_config(EndpointConfigName=config_name)
                log.info("Deleted endpoint config %s", config_name)
            except ClientError as exc:
                log.warning("Failed to delete endpoint config %s: %s", config_name, exc)
        if args.delete_model and model_name:
            sm_client = boto3.client("sagemaker", region_name=region)
            try:
                sm_client.delete_model(ModelName=model_name)
                log.info("Deleted model %s", model_name)
            except ClientError as exc:
                log.warning("Failed to delete model %s: %s", model_name, exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the VapeWatch SageMaker endpoint.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to deployment config JSON.")
    parser.add_argument("--region", help="AWS region override.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy = subparsers.add_parser("deploy", help="Package and deploy the model.")
    deploy.add_argument("--role-arn")
    deploy.add_argument("--endpoint-name")
    deploy.add_argument("--model-name")
    deploy.add_argument("--model-data", help="Existing S3 URI of the model.tar.gz artifact.")
    deploy.add_argument("--artifact-bucket", help="Bucket to upload the local artifact to.")
    deploy.add_argument("--artifact-prefix", help="Prefix for uploaded artifact.")
    deploy.add_argument("--artifact-path", help="Path to local model.tar.gz (defaults to model.tar.gz next to this script).")
    deploy.add_argument("--instance-type", help="Endpoint instance type (default ml.m5.large).")
    deploy.add_argument("--instance-count", type=int, help="Number of instances (default 1).")
    deploy.add_argument("--source-dir", help="Path to the inference source directory.")
    deploy.add_argument("--entry-point", help="Inference entry point (default inference.py).")
    deploy.add_argument("--framework-version", help="PyTorch container version (default 2.0.0).")
    deploy.add_argument("--py-version", help="Python version (default py310).")
    deploy.add_argument("--no-wait", action="store_true", help="Do not wait for endpoint creation to finish.")

    test = subparsers.add_parser("test", help="Send a sample image to the endpoint.")
    test.add_argument("--endpoint-name")
    test.add_argument("--image", help="Path to the image to send.")
    test.add_argument("--confidence", type=float, help="Confidence threshold (default 0.5).")

    list_cmd = subparsers.add_parser("list", help="List SageMaker endpoints in the region.")
    list_cmd.add_argument("--name-contains", help="Filter endpoints by substring.")

    delete = subparsers.add_parser("delete", help="Delete the endpoint (and optionally the config/model).")
    delete.add_argument("--endpoint-name")
    delete.add_argument("--endpoint-config", help="Endpoint config name (defaults to endpoint name).")
    delete.add_argument("--model-name", help="Model name to delete.")
    delete.add_argument("--delete-config", action="store_true", help="Delete the endpoint config as well.")
    delete.add_argument("--delete-model", action="store_true", help="Delete the SageMaker model as well.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = _load_config(Path(args.config))

    if args.command == "deploy":
        deploy_endpoint(args, config)
    elif args.command == "test":
        test_endpoint(args, config)
    elif args.command == "list":
        list_endpoints(args, config)
    elif args.command == "delete":
        delete_endpoint(args, config)
    else:  # pragma: no cover - argparser enforces valid commands
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
