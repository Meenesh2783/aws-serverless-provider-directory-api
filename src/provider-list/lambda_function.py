"""List providers with optional filters for GET /providers.

Empty filter values (for example, ``?city=``) are rejected with HTTP 400.
"""
import json
import logging
import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

_TABLE = None
_ALLOWED_STATUSES = {"ACTIVE", "INACTIVE"}
_MAX_ITEMS = 100


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


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _response(status_code: int, payload: dict[str, Any]):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=_json_default),
    }


def _log_request(
    request_id: str,
    outcome: str,
    caller: str,
    error: str | None = None,
) -> None:
    record = {
        "request_id": request_id,
        "outcome": outcome,
        "caller": caller,
    }
    if error is not None:
        record["error"] = error
    logger.info(json.dumps(record, separators=(",", ":")))


def _invalid(request_id: str, caller: str, message: str):
    _log_request(request_id, "invalid", caller)
    return _response(400, {"message": message})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", "unknown")
    caller = _get_caller_sub(event) if isinstance(event, dict) else "unknown"

    try:
        params = (
            event.get("queryStringParameters")
            if isinstance(event, dict)
            else None
        )
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _invalid(
                request_id, caller, "Query parameters must be an object"
            )

        filters: dict[str, str] = {}
        for name in ("city", "specialty", "status"):
            value = params.get(name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                return _invalid(
                    request_id,
                    caller,
                    f"Query parameter {name} must not be empty",
                )
            normalized = value.strip()
            filters[name] = (
                normalized.upper() if name == "status" else normalized.lower()
            )

        if "status" in filters and filters["status"] not in _ALLOWED_STATUSES:
            return _invalid(
                request_id,
                caller,
                "Query parameter status must be ACTIVE or INACTIVE",
            )

        filter_expression = None
        for name, value in filters.items():
            condition = Attr(name).eq(value)
            filter_expression = (
                condition
                if filter_expression is None
                else filter_expression & condition
            )

        items: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {}
        if filter_expression is not None:
            scan_kwargs["FilterExpression"] = filter_expression

        while len(items) < _MAX_ITEMS:
            # Limit work and response size; DynamoDB may return fewer than Limit
            # after applying a filter, so continue while a scan cursor remains.
            scan_kwargs["Limit"] = _MAX_ITEMS - len(items)
            response = _get_table().scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        _log_request(request_id, "listed", caller)
        return _response(200, {"count": len(items), "items": items})

    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        _log_request(request_id, "error", caller, f"{code}: {error}")
        return _response(500, {"message": "Internal server error"})
    except Exception as error:
        _log_request(
            request_id,
            "error",
            caller,
            f"{type(error).__name__}: {error}",
        )
        return _response(500, {"message": "Internal server error"})