import json
import logging
import os
from decimal import Decimal

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

HEADERS = {"Content-Type": "application/json"}
DYNAMODB_PARTITION_KEY_MAX_BYTES = 2048


class DecimalEncoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, Decimal):
            return int(value) if value % 1 == 0 else float(value)
        return super().default(value)


def response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": HEADERS,
        "body": json.dumps(payload, cls=DecimalEncoder),
    }


def get_caller_sub(event):
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims") or {}
    return claims.get("sub") or "unknown"


def log_request(request_id, provider_id, outcome, caller, **details):
    logger.info(json.dumps({
        "request_id": request_id,
        "provider_id": provider_id,
        "outcome": outcome,
        "caller": caller,
        **details,
    }))


def lambda_handler(event, context):
    request_id = getattr(context, "aws_request_id", None)
    caller = get_caller_sub(event)

    path_parameters = event.get("pathParameters") or {}
    raw_provider_id = path_parameters.get("id")

    if not isinstance(raw_provider_id, str):
        log_request(request_id, None, "invalid", caller)
        return response(400, {"message": "Provider ID is required"})

    provider_id = raw_provider_id.strip().lower()

    if (
        not provider_id
        or len(provider_id.encode("utf-8")) > DYNAMODB_PARTITION_KEY_MAX_BYTES
    ):
        log_request(request_id, provider_id or None, "invalid", caller)
        return response(400, {"message": "Invalid provider ID"})

    try:
        table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
        result = table.get_item(Key={"provider_id": provider_id})
    except (ClientError, BotoCoreError, KeyError):
        logger.exception(
            "DynamoDB get_item failed",
            extra={"request_id": request_id, "caller": caller},
        )
        log_request(request_id, provider_id, "error", caller)
        return response(500, {"message": "Internal server error"})

    item = result.get("Item")
    if item is None:
        log_request(request_id, provider_id, "not_found", caller)
        return response(404, {"message": "Provider not found"})

    log_request(request_id, provider_id, "found", caller)
    return response(200, item)