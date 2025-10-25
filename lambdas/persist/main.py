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


def lambda_handler(event, ctx):
    report_id = _clean_string(event.get("request_id")) or str(uuid.uuid4())
    submitted_at = _isoformat_now()
    notes = _clean_string(event.get("notes"), 2000)
    content_type = _clean_string(event.get("content_type"), 255)
    filename = _clean_string(event.get("filename"), 255)

    location = _prepare_location(event.get("location"))
    weather_snapshot = _prepare_weather(event.get("weather_snapshot"))

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
    }

    # DynamoDB does not allow empty map attributes.
    if not item["assets"]:
        item.pop("assets")

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
    if weather_snapshot:
        event["weather_snapshot"] = weather_snapshot
    return event
