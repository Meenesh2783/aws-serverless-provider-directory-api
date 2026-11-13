import json
import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

_TABLE = None
_REQUIRED_FIELDS = ("provider_id", "name", "specialty", "city", "status")
_ALLOWED_STATUSES = {"ACTIVE", "INACTIVE"}
_MAX_LENGTHS = {
    "provider_id": 64,
    "name": 100,
    "specialty": 100,
    "city": 100,
    "status": 8,
}
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _get_table():
    """Create the DynamoDB table handle lazily for Lambda reuse."""
    global _TABLE
    if _TABLE is None:
        table_name = os.environ.get("TABLE_NAME")
        if not table_name:
            raise RuntimeError("TABLE_NAME environment variable is not set")
        _TABLE = boto3.resource("dynamodb").Table(table_name)
    return _TABLE


def _response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=_json_default),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )


def _log_request(
    request_id: str,
    provider_id: str | None,
    outcome: str,
    *,
    error: str | None = None,
) -> None:
    record = {
        "request_id": request_id,
        "provider_id": provider_id,
        "outcome": outcome,
    }
    if error is not None:
        record["error"] = error
    logger.info(json.dumps(record, separators=(",", ":")))


def _invalid(request_id: str, provider_id: str | None, message: str):
    _log_request(request_id, provider_id, "invalid")
    return _response(400, {"message": message})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Create a provider item and return an API Gateway proxy response."""
    request_id = getattr(context, "aws_request_id", "unknown")
    provider_id = None

    try:
        raw_body = event.get("body") if isinstance(event, dict) else None
        if raw_body is None:
            return _invalid(request_id, provider_id, "Request body is required")
        if not isinstance(raw_body, str):
            return _invalid(
                request_id, provider_id, "Request body must be a JSON string"
            )

        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return _invalid(
                request_id, provider_id, "Request body must contain valid JSON"
            )

        if not isinstance(body, dict):
            return _invalid(
                request_id, provider_id, "Request body must be a JSON object"
            )

        provider_id_value = body.get("provider_id")
        if isinstance(provider_id_value, str):
            provider_id = provider_id_value.strip() or None

        item: dict[str, str] = {}
        for field in _REQUIRED_FIELDS:
            value = body.get(field)

            if value is None or (isinstance(value, str) and not value.strip()):
                return _invalid(
                    request_id,
                    provider_id,
                    f"Missing required field: {field}",
                )

            if not isinstance(value, str):
                return _invalid(
                    request_id,
                    provider_id,
                    f"Field {field} must be a non-empty string",
                )

            item[field] = value.strip()

        provider_id = item["provider_id"]

        for field, value in item.items():
            if len(value) > _MAX_LENGTHS[field]:
                return _invalid(
                    request_id,
                    provider_id,
                    f"Field {field} must be at most "
                    f"{_MAX_LENGTHS[field]} characters",
                )

        if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
            return _invalid(
                request_id,
                provider_id,
                "Field provider_id must start with a letter or digit and "
                "contain only letters, digits, hyphens, or underscores",
            )

        # Store IDs in lowercase.
        item["provider_id"] = provider_id.lower()
        provider_id = item["provider_id"]

        # Accept lowercase or mixed-case status and store it canonically.
        item["status"] = item["status"].upper()
        if item["status"] not in _ALLOWED_STATUSES:
            return _invalid(
                request_id,
                provider_id,
                "Field status must be ACTIVE or INACTIVE",
            )

        item["specialty"] = item["specialty"].lower()
        item["city"] = item["city"].lower()
        item["updated_at"] = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

        _get_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(provider_id)",
        )

        _log_request(request_id, provider_id, "created")
        return _response(201, {"message": "Provider created", "item": item})

    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")

        if code == "ConditionalCheckFailedException":
            _log_request(request_id, provider_id, "duplicate")
            return _response(409, {"message": "Provider already exists"})

        _log_request(
            request_id,
            provider_id,
            "error",
            error=f"{code}: {error}",
        )
        return _response(500, {"message": "Internal server error"})

    except Exception as error:
        _log_request(
            request_id,
            provider_id,
            "error",
            error=f"{type(error).__name__}: {error}",
        )
        return _response(500, {"message": "Internal server error"})