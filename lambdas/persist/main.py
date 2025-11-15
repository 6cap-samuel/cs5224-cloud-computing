import datetime as dt
import logging
import math
import os
import uuid
from decimal import Decimal, InvalidOperation

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

_TABLE_NAME = os.environ["REPORTS_TABLE"]
_ALERTS_TOPIC = os.environ.get("ALERTS_TOPIC")
_MAX_STORED_DETECTIONS = 25

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(_TABLE_NAME)

sns = boto3.client("sns") if _ALERTS_TOPIC else None


def _isoformat_now() -> str:
    now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _safe_decimal(value, precision: int | None = None) -> Decimal | None:
    if value is None:
        return None
    try:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            if precision is not None:
                formatted = f"{value:.{precision}f}"
                return Decimal(formatted)
            return Decimal(str(value))
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, str):
            if not value.strip():
                return None
            return Decimal(value)
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _prepare_location(location) -> dict | None:
    if not isinstance(location, dict):
        return None
    lat = _safe_decimal(location.get("latitude"), precision=6)
    lon = _safe_decimal(location.get("longitude"), precision=6)
    if lat is None or lon is None:
        return None

    loc = {"latitude": lat, "longitude": lon}
    accuracy = _safe_decimal(location.get("accuracy_m"), precision=2)
    if accuracy is not None:
        if accuracy < 0:
            accuracy = None
        elif accuracy > Decimal("10000"):
            accuracy = Decimal("10000")
    if accuracy is not None:
        loc["accuracy_m"] = accuracy
    captured_at = location.get("captured_at")
    if isinstance(captured_at, str) and captured_at.strip():
        loc["captured_at"] = captured_at[:40]
    return loc


def _prepare_location_context(context) -> dict | None:
    if not isinstance(context, dict):
        return None

    context_map: dict[str, object] = {}

    lamppost = context.get("nearest_lamppost")
    if isinstance(lamppost, dict):
        nearest = _strip_none(
            {
                "id": _clean_string(lamppost.get("id"), 120),
                "name": _clean_string(lamppost.get("name"), 255),
            }
        )
        distance = _safe_decimal(lamppost.get("distance_m"), precision=2)
        if distance is not None and distance >= 0:
            nearest["distance_m"] = distance
        lat = _safe_decimal(lamppost.get("latitude"), precision=6)
        lon = _safe_decimal(lamppost.get("longitude"), precision=6)
        if lat is not None and lon is not None:
            nearest["latitude"] = lat
            nearest["longitude"] = lon
        if nearest:
            context_map["nearest_lamppost"] = nearest

    park = context.get("nearest_park")
    if isinstance(park, dict):
        park_info = _strip_none(
            {
                "id": _clean_string(park.get("id"), 120),
                "name": _clean_string(park.get("name"), 255),
                "type": _clean_string(park.get("type"), 120),
            }
        )
        distance = _safe_decimal(park.get("distance_m"), precision=2)
        if distance is not None and distance >= 0:
            park_info["distance_m"] = distance
        lat = _safe_decimal(park.get("latitude"), precision=6)
        lon = _safe_decimal(park.get("longitude"), precision=6)
        if lat is not None and lon is not None:
            park_info["latitude"] = lat
            park_info["longitude"] = lon
        if park_info:
            context_map["nearest_park"] = park_info

    return context_map or None


def _prepare_weather(snapshot) -> dict | None:
    if not isinstance(snapshot, dict):
        return None

    prepared: dict[str, object] = {}

    source = _clean_string(snapshot.get("source"), 255)
    if source:
        prepared["source"] = source

    fetched_at = _clean_string(snapshot.get("fetched_at"), 64)
    if fetched_at:
        prepared["fetched_at"] = fetched_at

    api_update = _clean_string(snapshot.get("api_update_timestamp"), 64)
    if api_update:
        prepared["api_update_timestamp"] = api_update

    api_timestamp = _clean_string(snapshot.get("api_timestamp"), 64)
    if api_timestamp:
        prepared["api_timestamp"] = api_timestamp

    api_status = _clean_string(snapshot.get("api_status"), 64)
    if api_status:
        prepared["api_status"] = api_status

    valid_period = snapshot.get("valid_period")
    if isinstance(valid_period, dict):
        start = _clean_string(valid_period.get("start"), 64)
        end = _clean_string(valid_period.get("end"), 64)
        period = _strip_none({"start": start, "end": end})
        if period:
            prepared["valid_period"] = period

    nearest_area = snapshot.get("nearest_area")
    if isinstance(nearest_area, dict):
        nearest = _strip_none(
            {
                "area": _clean_string(nearest_area.get("area"), 120),
                "forecast": _clean_string(nearest_area.get("forecast"), 500),
            }
        )
        distance = _safe_decimal(nearest_area.get("distance_km"), precision=2)
        if distance is not None and distance >= 0:
            nearest["distance_km"] = distance
        if nearest:
            prepared["nearest_area"] = nearest

    forecasts = snapshot.get("forecasts")
    if isinstance(forecasts, list):
        cleaned_forecasts = []
        for entry in forecasts:
            if not isinstance(entry, dict):
                continue
            cleaned = _strip_none(
                {
                    "area": _clean_string(entry.get("area"), 120),
                    "forecast": _clean_string(entry.get("forecast"), 500),
                }
            )
            if cleaned:
                cleaned_forecasts.append(cleaned)
        if cleaned_forecasts:
            prepared["forecasts"] = cleaned_forecasts

    total_forecasts = snapshot.get("total_forecasts")
    try:
        total_forecasts = int(total_forecasts)
    except (TypeError, ValueError):
        total_forecasts = None
    if total_forecasts is not None and total_forecasts >= 0:
        prepared["total_forecasts"] = total_forecasts

    return prepared or None


def _clean_string(value, max_length: int | None = None):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        return None
    if max_length is not None:
        return value[:max_length]
    return value


def _strip_none(mapping: dict) -> dict:
    return {key: value for key, value in mapping.items() if value is not None}


def _ensure_dict(value):
    return value if isinstance(value, dict) else {}


def _coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _first_non_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _prepare_detections(source) -> list[dict] | None:
    detections = None
    if isinstance(source, list):
        detections = source
    elif isinstance(source, dict):
        inner = source.get("detections")
        if isinstance(inner, list):
            detections = inner
    if not isinstance(detections, list):
        return None

    sanitized: list[dict] = []
    for entry in detections[:_MAX_STORED_DETECTIONS]:
        if not isinstance(entry, dict):
            continue
        detection: dict[str, object] = {}
        label = _clean_string(entry.get("class_name"), 120)
        if label:
            detection["class_name"] = label
        try:
            cls_id = int(entry.get("class_id"))
        except (TypeError, ValueError):
            cls_id = None
        if cls_id is not None:
            detection["class_id"] = cls_id
        confidence = _safe_decimal(entry.get("confidence"), precision=4)
        if confidence is not None:
            detection["confidence"] = confidence
        bbox = entry.get("bbox")
        if isinstance(bbox, dict):
            bbox_payload = {}
            for coord in ("x1", "y1", "x2", "y2"):
                coord_value = _safe_decimal(bbox.get(coord), precision=None)
                if coord_value is not None:
                    bbox_payload[coord] = coord_value
            if bbox_payload:
                detection["bbox"] = bbox_payload
        if detection:
            sanitized.append(detection)
    return sanitized or None


def _prepare_inference(event, detections: list[dict] | None) -> dict | None:
    inference = event.get("inference")
    if not isinstance(inference, dict):
        inference = {}

    threshold_raw = _first_non_none(
        event.get("confidence_threshold"),
        inference.get("confidence_threshold"),
        (inference.get("result") or {}).get("confidence_threshold"),
    )
    threshold = _safe_decimal(threshold_raw, precision=4)

    endpoint = _clean_string(inference.get("endpoint"), 255)
    result_block = inference.get("result")
    if not isinstance(result_block, dict):
        result_block = {}

    total_raw = _first_non_none(
        event.get("total_detections"),
        result_block.get("total_detections"),
        len(detections or []),
    )
    total_detections = _safe_decimal(total_raw, precision=0)

    vape_detected = _first_non_none(
        _coerce_bool(event.get("vape_detected")),
        _coerce_bool(result_block.get("vape_detected")),
    )
    cigarette_detected = _first_non_none(
        _coerce_bool(event.get("cigarette_detected")),
        _coerce_bool(result_block.get("cigarette_detected")),
    )

    faces_blurred = _safe_decimal(
        _first_non_none(event.get("faces_blurred"), result_block.get("faces_blurred")),
        precision=0,
    )

    prepared_result: dict[str, object] = {}
    if detections:
        prepared_result["detections"] = detections
    if faces_blurred is not None:
        prepared_result["faces_blurred"] = faces_blurred

    inference_payload: dict[str, object] = {}
    if endpoint:
        inference_payload["endpoint"] = endpoint
    if threshold is not None:
        inference_payload["confidence_threshold"] = threshold
    if total_detections is not None:
        inference_payload["total_detections"] = total_detections
    if vape_detected is not None:
        inference_payload["vape_detected"] = vape_detected
    if cigarette_detected is not None:
        inference_payload["cigarette_detected"] = cigarette_detected
    if prepared_result:
        inference_payload["result"] = prepared_result

    return inference_payload or None


def lambda_handler(event, ctx):
    report_id = _clean_string(event.get("request_id")) or str(uuid.uuid4())
    submitted_at = _isoformat_now()
    notes = _clean_string(event.get("notes"), 2000)
    content_type = _clean_string(event.get("content_type"), 255)
    filename = _clean_string(event.get("filename"), 255)

    location = _prepare_location(event.get("location"))
    weather_snapshot = _prepare_weather(event.get("weather_snapshot"))
    location_context = _prepare_location_context(event.get("location_context"))

    raw_inference = _ensure_dict(event.get("inference"))
    detections = _prepare_detections(event.get("detections") or raw_inference.get("result"))
    inference_payload = _prepare_inference(event, detections)

    item = {
        "report_id": report_id,
        "submitted_at": submitted_at,
        "status": _clean_string(event.get("status"), 64) or "PENDING_REVIEW",
        "notes": notes,
        "content_type": content_type,
        "filename": filename,
        "raw_object_bucket": _clean_string(event.get("raw_object_bucket"), 255),
        "raw_object_key": _clean_string(event.get("raw_object_key"), 512),
        "assets": _ensure_dict(event.get("assets")),
        "ingest_error": _clean_string(event.get("ingest_error"), 64),
        "location": location,
        "weather_snapshot": weather_snapshot,
        "location_context": location_context,
        "inference": inference_payload,
        "detections": detections,
    }

    if inference_payload:
        if inference_payload.get("total_detections") is not None:
            item["total_detections"] = inference_payload["total_detections"]
        if inference_payload.get("vape_detected") is not None:
            item["vape_detected"] = inference_payload["vape_detected"]
        if inference_payload.get("cigarette_detected") is not None:
            item["cigarette_detected"] = inference_payload["cigarette_detected"]

    # DynamoDB does not allow empty map attributes.
    if not item["assets"]:
        item.pop("assets")
    if item.get("detections") is None:
        item.pop("detections", None)
    if item.get("inference") is None:
        item.pop("inference", None)

    item = _strip_none(item)

    log.info("Persisting report %s with keys %s", report_id, list(item.keys()))

    table.put_item(Item=item)

    if sns and _ALERTS_TOPIC and item.get("status") == "PENDING_REVIEW":
        try:
            sns.publish(
                TopicArn=_ALERTS_TOPIC,
                Message=f"New VapeWatch report {report_id} awaiting review.",
                Subject="New VapeWatch Report",
            )
        except Exception:
            log.exception("Failed to publish alert for report %s", report_id)

    event["report_id"] = report_id
    event["submitted_at"] = submitted_at
    if location:
        event["location"] = {
            "latitude": float(location["latitude"]),
            "longitude": float(location["longitude"]),
            **(
                {"accuracy_m": float(location["accuracy_m"])}
                if "accuracy_m" in location
                else {}
            ),
        }
        if "captured_at" in location:
            event["location"]["captured_at"] = location["captured_at"]
    if location_context:
        context_payload: dict[str, dict] = {}
        lamppost = location_context.get("nearest_lamppost")
        if lamppost:
            lamp_payload = {
                k: lamppost[k]
                for k in ("id", "name")
                if k in lamppost
            }
            if "distance_m" in lamppost:
                lamp_payload["distance_m"] = float(lamppost["distance_m"])
            if "latitude" in lamppost and "longitude" in lamppost:
                lamp_payload["latitude"] = float(lamppost["latitude"])
                lamp_payload["longitude"] = float(lamppost["longitude"])
            if lamp_payload:
                context_payload["nearest_lamppost"] = lamp_payload
        park = location_context.get("nearest_park")
        if park:
            park_payload = {
                k: park[k]
                for k in ("id", "name", "type")
                if k in park
            }
            if "distance_m" in park:
                park_payload["distance_m"] = float(park["distance_m"])
            if "latitude" in park and "longitude" in park:
                park_payload["latitude"] = float(park["latitude"])
                park_payload["longitude"] = float(park["longitude"])
            if park_payload:
                context_payload["nearest_park"] = park_payload
        if context_payload:
            event["location_context"] = context_payload
    if weather_snapshot:
        event["weather_snapshot"] = weather_snapshot
    if detections:
        event["detections"] = detections
    if inference_payload:
        event["inference"] = inference_payload
        if inference_payload.get("total_detections") is not None:
            event["total_detections"] = inference_payload["total_detections"]
        if inference_payload.get("vape_detected") is not None:
            event["vape_detected"] = inference_payload["vape_detected"]
        if inference_payload.get("cigarette_detected") is not None:
            event["cigarette_detected"] = inference_payload["cigarette_detected"]
    return event
