import base64
import datetime as dt
import os
import re
import uuid

import boto3

s3 = boto3.client("s3")
RAW_BUCKET = os.environ["RAW_BUCKET"]
EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET")

_FILENAME_CLEAN_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _clean_filename(filename: str) -> str:
    sanitized = _FILENAME_CLEAN_PATTERN.sub("-", filename or "upload.bin")
    return sanitized.strip("-") or "upload.bin"


def lambda_handler(event, ctx):
    photo_b64 = event.pop("photo_base64", None)
    if not photo_b64:
        return event

    if "base64," in photo_b64:
        photo_b64 = photo_b64.split("base64,", 1)[1]

    try:
        binary = base64.b64decode(photo_b64, validate=True)
    except Exception:
        event["ingest_error"] = "INVALID_BASE64"
        return event

    filename = _clean_filename(event.get("filename", "evidence.bin"))
    content_type = event.get("content_type", "application/octet-stream")
    request_id = event.get("request_id") or str(uuid.uuid4())
    timestamp = dt.datetime.utcnow().strftime("%Y/%m/%d/%H%M%S")
    s3_key = f"{timestamp}/{request_id}/{filename}"

    metadata = {}
    if event.get("notes"):
        metadata["notes"] = event["notes"][:512]
    metadata["request_id"] = request_id

    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=s3_key,
        Body=binary,
        ContentType=content_type,
        Metadata=metadata,
    )

    event["raw_object_bucket"] = RAW_BUCKET
    event["raw_object_key"] = s3_key
    assets = event.setdefault("assets", {})
    assets["raw"] = {"bucket": RAW_BUCKET, "key": s3_key}
    event["content_type"] = content_type

    # Placeholder for future redaction logic (e.g., generating sanitized copy).
    if EVIDENCE_BUCKET:
        assets["evidence_bucket"] = EVIDENCE_BUCKET

    return event
