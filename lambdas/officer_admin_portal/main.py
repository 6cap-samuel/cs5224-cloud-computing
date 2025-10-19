import base64
import json
import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

_TABLE_NAME = os.environ["REPORTS_TABLE"]
_RAW_BUCKET_ENV = os.environ.get("RAW_BUCKET")
_SIGNED_TTL = int(os.environ.get("SIGNED_URL_TTL", "900"))

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(_TABLE_NAME)

s3 = boto3.client("s3")


def _sanitize_limit(raw: Any, default: int = 25, minimum: int = 1, maximum: int = 100) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _decode_cursor(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        padding = "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(raw + padding).decode("utf-8")
        data = json.loads(decoded)
        if not isinstance(data, dict):
            return None
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        log.exception("Failed to decode pagination cursor %s", raw)
        return None


def _encode_cursor(key: Optional[Dict[str, Any]]) -> Optional[str]:
    if not key:
        return None
    try:
        payload = json.dumps(key, separators=(",", ":"), ensure_ascii=False)
        encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")
        return encoded.rstrip("=")
    except Exception:
        log.exception("Failed to encode cursor %s", key)
        return None


def _coerce(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce(v) for v in value]
    return value


def _build_presigned_url(item: Dict[str, Any]) -> Optional[str]:
    bucket = item.get("raw_object_bucket") or (item.get("assets", {}).get("raw", {}).get("bucket")) or _RAW_BUCKET_ENV
    key = item.get("raw_object_key") or (item.get("assets", {}).get("raw", {}).get("key"))
    if not bucket or not key:
        return None
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_SIGNED_TTL,
        )
    except Exception:
        log.exception("Failed to generate presigned URL for %s/%s", bucket, key)
        return None


def _reshape_item(item: Dict[str, Any]) -> Dict[str, Any]:
    record = _coerce(item)
    record["image_url"] = _build_presigned_url(record)
    record.pop("raw_object_bucket", None)
    record.pop("raw_object_key", None)
    return record


def lambda_handler(event, _ctx):
    params = event.get("queryStringParameters") or {}
    limit = _sanitize_limit(params.get("limit"))
    cursor = _decode_cursor(params.get("cursor"))

    scan_kwargs: Dict[str, Any] = {"Limit": limit}
    if cursor:
        scan_kwargs["ExclusiveStartKey"] = cursor

    try:
        response = table.scan(**scan_kwargs)
    except Exception:
        log.exception("Failed to scan reports table")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "dynamodb_unavailable"}),
        }

    items = [_reshape_item(item) for item in response.get("Items", [])]
    items.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)

    next_cursor = _encode_cursor(response.get("LastEvaluatedKey"))

    payload = {
        "items": items,
        "count": len(items),
        "next_cursor": next_cursor,
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(payload),
    }
