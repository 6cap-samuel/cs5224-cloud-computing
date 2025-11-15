import base64
import datetime as dt
import os
import re
import uuid
import io

import boto3
from PIL import Image, ImageFilter

s3 = boto3.client("s3")
rekognition = boto3.client("rekognition")
RAW_BUCKET = os.environ["RAW_BUCKET"]
EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET")
FACE_BLUR_RADIUS = int(os.environ.get("FACE_BLUR_RADIUS", "35"))

_FILENAME_CLEAN_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _clean_filename(filename: str) -> str:
    sanitized = _FILENAME_CLEAN_PATTERN.sub("-", filename or "upload.bin")
    return sanitized.strip("-") or "upload.bin"


def _blur_faces(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size

    response = rekognition.detect_faces(Image={"Bytes": image_bytes}, Attributes=["DEFAULT"])
    faces = response.get("FaceDetails") or []
    if not faces:
        return image_bytes

    edited = image.copy()
    for face in faces:
        box = face.get("BoundingBox") or {}
        left = int(box.get("Left", 0) * width)
        top = int(box.get("Top", 0) * height)
        w = int(box.get("Width", 0) * width)
        h = int(box.get("Height", 0) * height)
        if w <= 0 or h <= 0:
            continue
        region = edited.crop((left, top, left + w, top + h))
        blurred = region.filter(ImageFilter.GaussianBlur(FACE_BLUR_RADIUS))
        edited.paste(blurred, (left, top))

    buffer = io.BytesIO()
    edited.save(buffer, format="JPEG")
    return buffer.getvalue()


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

    filename = _clean_filename(event.get("filename", "evidence.jpg"))
    content_type = event.get("content_type", "image/jpeg")
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

    if EVIDENCE_BUCKET:
        try:
            redacted = _blur_faces(binary)
        except Exception:
            redacted = binary
        evidence_key = f"{timestamp}/{request_id}/redacted_{filename}"
        s3.put_object(
            Bucket=EVIDENCE_BUCKET,
            Key=evidence_key,
            Body=redacted,
            ContentType="image/jpeg",
            Metadata={"request_id": request_id},
        )
        assets["evidence"] = {"bucket": EVIDENCE_BUCKET, "key": evidence_key}

    return event
