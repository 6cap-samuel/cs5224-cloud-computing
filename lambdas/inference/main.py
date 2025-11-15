import base64
import json
import logging
import os
from typing import Any, Dict, Tuple

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

_ENDPOINT_NAME = os.environ["SAGEMAKER_ENDPOINT_NAME"]
_DEFAULT_THRESHOLD = float(os.environ.get("INFERENCE_CONFIDENCE_THRESHOLD", "0.5"))

s3 = boto3.client("s3")
runtime = boto3.client("sagemaker-runtime")


def _resolve_source(event: Dict[str, Any]) -> Tuple[str | None, str | None]:
    bucket = event.get("raw_object_bucket")
    key = event.get("raw_object_key")
    if bucket and key:
        return bucket, key

    assets = event.get("assets") or {}
    raw = assets.get("raw") or {}
    bucket = bucket or raw.get("bucket")
    key = key or raw.get("key")
    return bucket, key


def _confidence_threshold(event: Dict[str, Any]) -> float:
    value = event.get("confidence_threshold")
    if value is None:
        inference_block = event.get("inference")
        if isinstance(inference_block, dict):
            value = inference_block.get("confidence_threshold")
    if value is None:
        value = _DEFAULT_THRESHOLD
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD


def _download_image(bucket: str, key: str) -> bytes:
    response = s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def _invoke_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    response = runtime.invoke_endpoint(
        EndpointName=_ENDPOINT_NAME,
        ContentType="application/json",
        Accept="application/json",
        Body=json.dumps(payload),
    )
    body = response["Body"].read()
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def lambda_handler(event, ctx):
    if not isinstance(event, dict):
        raise ValueError("Event payload must be a dict")

    bucket, key = _resolve_source(event)
    if not bucket or not key:
        raise ValueError("raw_object_bucket/raw_object_key are required for inference")

    log.info("Fetching raw image from s3://%s/%s", bucket, key)
    try:
        binary = _download_image(bucket, key)
    except Exception:
        log.exception("Failed to download raw artifact for inference")
        raise

    threshold = _confidence_threshold(event)
    payload = {
        "image": base64.b64encode(binary).decode("utf-8"),
        "confidence_threshold": threshold,
    }

    try:
        result = _invoke_endpoint(payload)
    except Exception:
        log.exception("SageMaker inference failed")
        raise

    event["inference"] = {
        "endpoint": _ENDPOINT_NAME,
        "confidence_threshold": threshold,
        "result": result,
    }
    event["detections"] = result.get("detections")
    event["vape_detected"] = result.get("vape_detected")
    event["cigarette_detected"] = result.get("cigarette_detected")
    event["total_detections"] = result.get("total_detections")
    return event
