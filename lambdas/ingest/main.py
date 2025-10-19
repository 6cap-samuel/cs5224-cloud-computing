import base64
import datetime as dt
import io
import json
import logging
import math
import os
import re
import uuid

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")
s3 = boto3.client("s3")
ARN = os.environ.get("STATE_MACHINE_ARN", "")
RAW_BUCKET = os.environ.get("RAW_BUCKET")
EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET")
JPEG_QUALITY = int(os.environ.get("IMAGE_JPEG_QUALITY", "85"))
PNG_COMPRESS_LEVEL = int(os.environ.get("IMAGE_PNG_COMPRESS_LEVEL", "6"))
MIN_COMPRESSION_RATIO = float(os.environ.get("IMAGE_MIN_COMPRESSION_RATIO", "0.95"))
MAX_LOCATION_ACCURACY_METERS = float(os.environ.get("MAX_LOCATION_ACCURACY_METERS", "10000"))

_FILENAME_CLEAN_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")

try:
    from PIL import Image
except Exception:  # Pillow is optional in the deployment package.
    Image = None


def _clean_filename(filename: str) -> str:
    sanitized = _FILENAME_CLEAN_PATTERN.sub("-", filename or "upload.bin")
    return sanitized.strip("-") or "upload.bin"


def _coerce_coordinate(value, minimum: float, maximum: float):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    if num < minimum or num > maximum:
        return None
    return round(num, 6)


def _sanitize_location(location) -> dict | None:
    if not isinstance(location, dict):
        return None

    lat = _coerce_coordinate(location.get("latitude"), -90.0, 90.0)
    lon = _coerce_coordinate(location.get("longitude"), -180.0, 180.0)
    if lat is None or lon is None:
        return None

    sanitized = {"latitude": lat, "longitude": lon}

    accuracy_raw = location.get("accuracy_m")
    try:
        accuracy = float(accuracy_raw)
    except (TypeError, ValueError):
        accuracy = None
    if accuracy is not None:
        if accuracy <= 0 or not math.isfinite(accuracy):
            accuracy = None
        elif accuracy > MAX_LOCATION_ACCURACY_METERS:
            accuracy = MAX_LOCATION_ACCURACY_METERS
        sanitized["accuracy_m"] = round(accuracy, 2) if accuracy is not None else None
    if sanitized.get("accuracy_m") is None:
        sanitized.pop("accuracy_m", None)

    captured_at = location.get("captured_at")
    if isinstance(captured_at, str) and captured_at:
        sanitized["captured_at"] = captured_at[:40]

    return sanitized


def _compress_photo(binary: bytes, content_type: str) -> tuple[bytes, str]:
    """Attempt to losslessly compress JPEG/PNG images while keeping quality reasonable."""
    if not Image or not binary:
        return binary, content_type

    try:
        with Image.open(io.BytesIO(binary)) as img:
            img_format = (img.format or "").upper()
            if img_format not in {"JPEG", "PNG"}:
                return binary, content_type

            output = io.BytesIO()
            if img_format == "JPEG":
                quality = max(10, min(95, JPEG_QUALITY))
                img.save(output, format="JPEG", optimize=True, quality=quality)
                new_content_type = "image/jpeg"
            else:  # PNG
                compress_level = max(0, min(9, PNG_COMPRESS_LEVEL))
                img.save(output, format="PNG", optimize=True, compress_level=compress_level)
                new_content_type = "image/png"

            compressed = output.getvalue()
    except Exception:  # pragma: no cover - best effort fallback
        log.exception("Image compression failed; uploading original bytes")
        return binary, content_type

    if len(compressed) >= len(binary) * MIN_COMPRESSION_RATIO:
        return binary, content_type

    log.info(
        "Compressed image from %d bytes to %d bytes using format %s",
        len(binary),
        len(compressed),
        new_content_type,
    )
    return compressed, new_content_type


def _handle_photo(body: dict, request_id: str) -> None:
    if not RAW_BUCKET:
        log.warning("RAW_BUCKET environment variable not set; skipping persistence")
        return

    photo_b64 = body.pop("photo_base64", None)
    if not photo_b64:
        return

    if "base64," in photo_b64:
        photo_b64 = photo_b64.split("base64,", 1)[1]

    try:
        binary = base64.b64decode(photo_b64, validate=True)
    except Exception:
        body["ingest_error"] = "INVALID_BASE64"
        log.exception("Failed to decode base64 payload for request %s", request_id)
        return

    filename = _clean_filename(body.get("filename", "evidence.bin"))
    content_type = body.get("content_type", "application/octet-stream")
    binary, content_type = _compress_photo(binary, content_type)
    timestamp = dt.datetime.utcnow().strftime("%Y/%m/%d/%H%M%S")
    s3_key = f"{timestamp}/{request_id}/{filename}"

    metadata = {"request_id": request_id}
    notes = body.get("notes")
    if isinstance(notes, str) and notes:
        metadata["notes"] = notes[:512]

    try:
        s3.put_object(
            Bucket=RAW_BUCKET,
            Key=s3_key,
            Body=binary,
            ContentType=content_type,
            Metadata=metadata,
        )
    except Exception:
        log.exception("Failed to persist photo for request %s", request_id)
        raise

    assets = body.setdefault("assets", {})
    assets["raw"] = {"bucket": RAW_BUCKET, "key": s3_key}
    if EVIDENCE_BUCKET:
        assets["evidence_bucket"] = EVIDENCE_BUCKET

    body["raw_object_bucket"] = RAW_BUCKET
    body["raw_object_key"] = s3_key
    body["content_type"] = content_type


def lambda_handler(event, ctx):
    body = event.get("body")
    try:
        body = json.loads(body) if isinstance(body, str) else (body or {})
    except Exception:
        log.exception("Invalid JSON body")
        return {"statusCode": 400, "body": json.dumps({"error": "invalid_json"})}

    if not isinstance(body, dict):
        return {"statusCode": 400, "body": json.dumps({"error": "invalid_payload"})}

    request_id = str(uuid.uuid4())
    body["request_id"] = request_id

    location = _sanitize_location(body.get("location"))
    if location:
        body["location"] = location
    elif "location" in body:
        body.pop("location", None)

    try:
        _handle_photo(body, request_id)
    except Exception:
        return {"statusCode": 500, "body": json.dumps({"error": "persist_failed"})}

    if ARN:
        try:
            sfn.start_execution(stateMachineArn=ARN, input=json.dumps(body))
        except Exception:
            log.exception("Failed to start state machine for request %s", request_id)
            return {"statusCode": 502, "body": json.dumps({"error": "pipeline_unavailable"})}

    return {"statusCode": 202, "body": json.dumps({"ok": True, "request_id": request_id})}
