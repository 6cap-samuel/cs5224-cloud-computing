import base64
import datetime as dt
import json
import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from boto3.dynamodb.conditions import Key

log = logging.getLogger()
log.setLevel(logging.INFO)

_TABLE_NAME = os.environ["REPORTS_TABLE"]
_RAW_BUCKET_ENV = os.environ.get("RAW_BUCKET")
_SIGNED_TTL = int(os.environ.get("SIGNED_URL_TTL", "900"))
_COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID")

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(_TABLE_NAME)

s3 = boto3.client("s3")
cognito = boto3.client("cognito-idp") if _COGNITO_USER_POOL_ID else None

_officer_cache: Dict[str, Dict[str, str]] = {}


def _json_response(status: int, body: Dict[str, Any], *, cache: bool = False) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
    }
    if not cache:
        headers["Cache-Control"] = "no-store"
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


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


def _sanitize_offset(raw: Any, default: int = 0) -> int:
    try:
        value = int(raw)
        if value < 0:
            return default
        return value
    except (TypeError, ValueError):
        return default


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


def _reshape_item(
    item: Dict[str, Any],
    *,
    enrich_officer: bool = True,
    include_history: bool = True,
) -> Dict[str, Any]:
    record = _coerce(item)
    record["image_url"] = _build_presigned_url(record)
    record.pop("raw_object_bucket", None)
    record.pop("raw_object_key", None)
    audit_block = record.get("audit")
    if isinstance(audit_block, dict) and enrich_officer:
        record["audit"] = _enrich_audit_block(audit_block)
    if not include_history:
        record.pop("audit_history", None)
    else:
        history = record.get("audit_history")
        if isinstance(history, list) and enrich_officer:
            record["audit_history"] = [
                _enrich_audit_block(entry) if isinstance(entry, dict) else entry
                for entry in history
            ]
    return record


def _enrich_officer_details(officer: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    if not officer:
        return officer

    if not _COGNITO_USER_POOL_ID or not cognito:
        return officer

    name = officer.get("name")
    email = officer.get("email")
    sub = officer.get("sub")
    username = officer.get("username") or sub
    if (name and email) or not username:
        return officer

    cached = _officer_cache.get(username)
    if cached:
        officer.update({k: v for k, v in cached.items() if v})
        return officer

    try:
        response = cognito.admin_get_user(UserPoolId=_COGNITO_USER_POOL_ID, Username=username)
    except cognito.exceptions.UserNotFoundException:
        log.warning("Officer %s not found in Cognito", username)
        return officer
    except Exception:
        log.exception("Failed to load officer metadata for %s", username)
        return officer

    attributes = {attr.get("Name"): attr.get("Value") for attr in response.get("UserAttributes", [])}
    username_value = response.get("Username")
    updates: Dict[str, Optional[str]] = {}
    if not officer.get("email"):
        updates["email"] = attributes.get("email") or attributes.get("custom:email")
    if not officer.get("name"):
        updates["name"] = (
            attributes.get("name")
            or attributes.get("preferred_username")
            or attributes.get("given_name")
            or attributes.get("family_name")
            or username_value
        )
    if username_value and not officer.get("username"):
        updates["username"] = username_value
    sanitized = {k: v for k, v in updates.items() if v}
    if sanitized:
        _officer_cache[username] = sanitized
        officer.update(sanitized)
    if officer.get("username") == officer.get("sub"):
        officer.pop("username", None)
    return officer


def _enrich_audit_block(block: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(block, dict):
        return block
    officer = block.get("officer")
    if isinstance(officer, dict):
        block["officer"] = _enrich_officer_details(officer.copy())
    return block


def _get_report_history(event: Dict[str, Any]) -> Dict[str, Any]:
    params = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    report_id = path_params.get("report_id")
    submitted_at = params.get("submitted_at")
    if not report_id or not submitted_at:
        return _json_response(400, {"error": "missing_report_identifier"})

    limit = _sanitize_limit(params.get("limit"), default=10, minimum=1, maximum=100)
    offset = _sanitize_offset(params.get("cursor"), default=0)

    try:
        response = table.get_item(
            Key={"report_id": report_id, "submitted_at": submitted_at},
            ConsistentRead=True,
        )
    except Exception:
        log.exception("Failed to load report %s history", report_id)
        return _json_response(500, {"error": "dynamodb_unavailable"})

    item = response.get("Item")
    if not item:
        return _json_response(404, {"error": "report_not_found"})

    shaped = _reshape_item(item)
    history = shaped.get("audit_history") or []
    ordered = list(reversed(history))
    total = len(ordered)
    start = offset if offset < total else total
    end = start + limit
    slice_items = ordered[start:end]
    next_cursor = end if end < total else None

    payload = {
        "items": slice_items,
        "count": len(slice_items),
        "total_count": total,
        "next_cursor": next_cursor,
    }
    return _json_response(200, payload)


def _list_reports(event: Dict[str, Any]) -> Dict[str, Any]:
    params = event.get("queryStringParameters") or {}
    limit = _sanitize_limit(params.get("limit"))
    cursor = _decode_cursor(params.get("cursor"))

    scan_kwargs: Dict[str, Any] = {"Limit": limit}
    projection = [
        "report_id",
        "submitted_at",
        "#status",
        "audit",
        "audit_updated_at",
        "#location",
        "location_context",
        "filename",
        "notes",
        "raw_object_bucket",
        "raw_object_key",
        "assets",
        "weather_snapshot",
    ]
    scan_kwargs["ProjectionExpression"] = ", ".join(projection)
    scan_kwargs["ExpressionAttributeNames"] = {
        "#status": "status",
        "#location": "location",
    }
    if cursor:
        scan_kwargs["ExclusiveStartKey"] = cursor

    try:
        response = table.scan(**scan_kwargs)
    except Exception:
        log.exception("Failed to scan reports table")
        return _json_response(500, {"error": "dynamodb_unavailable"})

    items = [
        _reshape_item(item, enrich_officer=False, include_history=False)
        for item in response.get("Items", [])
    ]
    items.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)

    payload = {
        "items": items,
        "count": len(items),
        "next_cursor": _encode_cursor(response.get("LastEvaluatedKey")),
    }
    return _json_response(200, payload)


def _sanitize_audit_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("invalid_payload")
    status = body.get("status")
    notes = body.get("notes")

    allowed_statuses = {"APPROVED", "REJECTED", "NEEDS_REVIEW"}
    if not isinstance(status, str) or status.upper() not in allowed_statuses:
        raise ValueError("invalid_status")
    clean_status = status.upper()

    clean_notes: Optional[str] = None
    if isinstance(notes, str):
        notes = notes.strip()
        if notes:
            clean_notes = notes[:2000]

    return {"status": clean_status, "notes": clean_notes}


def _extract_officer(event: Dict[str, Any]) -> Dict[str, Optional[str]]:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    officer_email = (
        claims.get("email")
        or claims.get("custom:email")
        or claims.get("preferred_username")
    )
    officer_name = (
        claims.get("name")
        or claims.get("preferred_username")
        or claims.get("given_name")
        or claims.get("cognito:username")
    )
    officer_sub = claims.get("sub")
    officer_username = claims.get("cognito:username") or officer_sub
    officer = {
        "email": officer_email,
        "name": officer_name,
        "sub": officer_sub,
        "username": officer_username,
    }
    return _enrich_officer_details(officer)


def _remove_none_values(mapping: Dict[str, Optional[str]]) -> Dict[str, str]:
    return {k: v for k, v in mapping.items() if v}


def _submit_audit(event: Dict[str, Any]) -> Dict[str, Any]:
    path_params = event.get("pathParameters") or {}
    report_id = path_params.get("report_id")
    if not report_id:
        return _json_response(400, {"error": "missing_report_id"})

    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return _json_response(400, {"error": "invalid_json"})

    try:
        payload = _sanitize_audit_payload(body)
    except ValueError as exc:
        if str(exc) == "invalid_status":
            return _json_response(400, {"error": "invalid_status"})
        return _json_response(400, {"error": "invalid_payload"})

    officer = _remove_none_values(_extract_officer(event))
    if not officer:
        officer = {"sub": "unknown"}

    timestamp = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    audit_entry = {
        "status": payload["status"],
        "timestamp": timestamp,
        "officer": officer,
    }
    if payload["notes"]:
        audit_entry["notes"] = payload["notes"]


    try:
        query_response = table.query(
            KeyConditionExpression=Key("report_id").eq(report_id),
            Limit=1,
            ConsistentRead=True,
        )
    except Exception:
        log.exception("Failed to load report %s before audit", report_id)
        return _json_response(500, {"error": "load_failed"})

    items = query_response.get("Items") or []
    if not items:
        return _json_response(404, {"error": "report_not_found"})

    item_key = items[0]
    if "report_id" not in item_key or "submitted_at" not in item_key:
        log.error("Report %s missing key attributes", report_id)
        return _json_response(500, {"error": "report_missing_submitted_at"})

    key_payload = {
        "report_id": item_key["report_id"],
        "submitted_at": item_key["submitted_at"],
    }

    update_expression = (
        "SET audit = :audit, "
        "audit_updated_at = :ts, "
        "audit_history = list_append(if_not_exists(audit_history, :empty_list), :entry_list)"
    )
    expression_values = {
        ":audit": audit_entry,
        ":ts": timestamp,
        ":entry_list": [audit_entry],
        ":empty_list": [],
    }

    try:
        result = table.update_item(
            Key=key_payload,
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ConditionExpression="attribute_exists(report_id) AND attribute_exists(submitted_at)",
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return _json_response(404, {"error": "report_not_found"})
    except Exception:
        log.exception("Failed to update audit for report %s", report_id)
        return _json_response(500, {"error": "audit_update_failed"})

    updated = result.get("Attributes") or {}
    shaped = _reshape_item(updated)
    return _json_response(200, {"report": shaped})


def lambda_handler(event, _ctx):
    method = (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", "GET")
        .upper()
    )
    route_key = event.get("routeKey") or ""

    if method == "GET" and route_key.startswith("GET /reports/") and route_key.endswith("/history"):
        return _get_report_history(event)

    if method == "GET":
        return _list_reports(event)
    if method == "POST":
        if route_key.startswith("POST /reports/") and route_key.endswith("/audit"):
            return _submit_audit(event)
        # Fallback: allow legacy integration on custom deployment stages where routeKey isn't set
        if event.get("pathParameters", {}).get("report_id"):
            return _submit_audit(event)
        return _json_response(400, {"error": "unsupported_operation"})

    return _json_response(405, {"error": "method_not_allowed"})
