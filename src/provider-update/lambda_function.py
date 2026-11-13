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
_REQUIRED_FIELDS = ("name", "specialty", "city", "status")
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


def _get_caller_sub(event: dict[str, Any]) -> str:
    """Return the Cognito caller sub, or 'unknown' if it is unavailable."""
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims") or {}
    return claims.get("sub") or "unknown"


def _response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=_json_default),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _log_request(
    request_id: str,
    provider_id: str | None,
    outcome: str,
    caller: str,
    *,
    error: str | None = None,
) -> None:
    record = {
        "request_id": request_id,
        "provider_id": provider_id,
        "outcome": outcome,
        "caller": caller,
    }
    if error is not None:
        record["error"] = error
    logger.info(json.dumps(record, separators=(",", ":")))


def _invalid(
    request_id: str,
    provider_id: str | None,
    caller: str,
    message: str,
):
    _log_request(request_id, provider_id, "invalid", caller)
    return _response(400, {"message": message})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Replace a provider item (PUT requires all four mutable fields)."""
    request_id = getattr(context, "aws_request_id", "unknown")
    caller = _get_caller_sub(event) if isinstance(event, dict) else "unknown"
    provider_id = None

    try:
        path_parameters = (
            event.get("pathParameters") if isinstance(event, dict) else None
        )
        if not isinstance(path_parameters, dict):
            return _invalid(
                request_id, provider_id, caller, "Path parameter id is required"
            )

        # The API Gateway route uses /providers/{id}; create/get/update all
        # canonicalize IDs to lowercase so existing lowercase keys keep working.
        path_id = path_parameters.get("id")
        if not isinstance(path_id, str) or not path_id.strip():
            return _invalid(
                request_id, provider_id, caller, "Path parameter id is required"
            )
        provider_id = path_id.strip()
        if (
            len(provider_id) > _MAX_LENGTHS["provider_id"]
            or not _PROVIDER_ID_PATTERN.fullmatch(provider_id)
        ):
            return _invalid(
                request_id,
                provider_id,
                caller,
                "Path parameter provider_id must start with a letter or digit and "
                "contain only letters, digits, hyphens, or underscores",
            )
        provider_id = provider_id.lower()

        raw_body = event.get("body")
        if raw_body is None:
            return _invalid(request_id, provider_id, caller, "Request body is required")
        if not isinstance(raw_body, str):
            return _invalid(
                request_id,
                provider_id,
                caller,
                "Request body must be a JSON string",
            )
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return _invalid(
                request_id,
                provider_id,
                caller,
                "Request body must contain valid JSON",
            )
        if not isinstance(body, dict):
            return _invalid(
                request_id,
                provider_id,
                caller,
                "Request body must be a JSON object",
            )

        body_id = body.get("provider_id")
        if body_id is not None:
            if not isinstance(body_id, str) or body_id.strip().lower() != provider_id:
                return _invalid(
                    request_id,
                    provider_id,
                    caller,
                    "Body provider_id must match path provider_id",
                )

        item: dict[str, str] = {"provider_id": provider_id}
        # PUT is a full replacement: name, specialty, city, and status are all required.
        for field in _REQUIRED_FIELDS:
            value = body.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                return _invalid(
                    request_id,
                    provider_id,
                    caller,
                    f"Missing required field: {field}",
                )
            if not isinstance(value, str):
                return _invalid(
                    request_id,
                    provider_id,
                    caller,
                    f"Field {field} must be a non-empty string",
                )
            item[field] = value.strip()

        for field, value in item.items():
            if len(value) > _MAX_LENGTHS[field]:
                return _invalid(
                    request_id,
                    provider_id,
                    caller,
                    f"Field {field} must be at most "
                    f"{_MAX_LENGTHS[field]} characters",
                )

        # Accept lowercase or mixed-case status consistently with create.
        item["status"] = item["status"].upper()
        if item["status"] not in _ALLOWED_STATUSES:
            return _invalid(
                request_id,
                provider_id,
                caller,
                "Field status must be ACTIVE or INACTIVE",
            )
        item["specialty"] = item["specialty"].lower()
        item["city"] = item["city"].lower()
        item["updated_at"] = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

        update_result = _get_table().update_item(
            Key={"provider_id": provider_id},
            UpdateExpression=(
                "SET #name = :name, specialty = :specialty, city = :city, "
                "#status = :status, updated_at = :updated_at"
            ),
            ConditionExpression="attribute_exists(provider_id)",
            ExpressionAttributeNames={"#name": "name", "#status": "status"},
            ExpressionAttributeValues={
                ":name": item["name"],
                ":specialty": item["specialty"],
                ":city": item["city"],
                ":status": item["status"],
                ":updated_at": item["updated_at"],
            },
            ReturnValues="ALL_NEW",
        )
        updated_item = update_result["Attributes"]
        _log_request(request_id, provider_id, "updated", caller)
        return _response(200, {"message": "Provider updated", "item": updated_item})

    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        if code == "ConditionalCheckFailedException":
            _log_request(request_id, provider_id, "not_found", caller)
            return _response(404, {"message": "Provider not found"})
        _log_request(
            request_id, provider_id, "error", caller, error=f"{code}: {error}"
        )
        return _response(500, {"message": "Internal server error"})

    except Exception as error:
        _log_request(
            request_id,
            provider_id,
            "error",
            caller,
            error=f"{type(error).__name__}: {error}",
        )
        return _response(500, {"message": "Internal server error"})