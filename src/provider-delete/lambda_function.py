"""Delete provider Lambda. API Gateway route: /providers/{id}."""
import json
import logging
import os
import re
from typing import Any

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

_TABLE = None
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_PROVIDER_ID_LENGTH = 64


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


def _response(status_code: int, payload: dict[str, Any] | None = None):
    response = {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
    }
    response["body"] = "" if payload is None else json.dumps(payload)
    return response


def _log_request(
    request_id: str,
    provider_id: str | None,
    outcome: str,
    caller: str,
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Hard delete a provider, returning 404 if its ID does not exist."""
    request_id = getattr(context, "aws_request_id", "unknown")
    caller = _get_caller_sub(event) if isinstance(event, dict) else "unknown"
    provider_id = None

    try:
        path_parameters = (
            event.get("pathParameters") if isinstance(event, dict) else None
        )
        path_id = (
            path_parameters.get("id")
            if isinstance(path_parameters, dict)
            else None
        )
        if not isinstance(path_id, str) or not path_id.strip():
            _log_request(request_id, provider_id, "invalid", caller)
            return _response(400, {"message": "Path parameter id is required"})

        provider_id = path_id.strip()
        if (
            len(provider_id) > _MAX_PROVIDER_ID_LENGTH
            or not _PROVIDER_ID_PATTERN.fullmatch(provider_id)
        ):
            _log_request(request_id, provider_id, "invalid", caller)
            return _response(400, {"message": "Path parameter id is invalid"})
        provider_id = provider_id.lower()

        _get_table().delete_item(
            Key={"provider_id": provider_id},
            ConditionExpression="attribute_exists(provider_id)",
        )
        _log_request(request_id, provider_id, "deleted", caller)
        return _response(204)

    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        if code == "ConditionalCheckFailedException":
            _log_request(request_id, provider_id, "not_found", caller)
            return _response(404, {"message": "Provider not found"})
        _log_request(
            request_id, provider_id, "error", caller, f"{code}: {error}"
        )
        return _response(500, {"message": "Internal server error"})

    except Exception as error:
        _log_request(
            request_id,
            provider_id,
            "error",
            caller,
            f"{type(error).__name__}: {error}",
        )
        return _response(500, {"message": "Internal server error"})