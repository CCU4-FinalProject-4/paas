import time
from decimal import Decimal
from functools import cache

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from . import config
from .metrics import DYNAMODB_LATENCY

THROTTLING_ERRORS = {
    "ProvisionedThroughputExceededException",
    "ThrottlingException",
    "RequestLimitExceeded",
}

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


@cache
def client():
    return boto3.client(
        "dynamodb",
        endpoint_url=config.AWS_ENDPOINT_URL,
        region_name=config.AWS_REGION,
        config=Config(
            retries={"mode": "adaptive", "max_attempts": 5},
            connect_timeout=2,
            read_timeout=5,
        ),
    )


def call(operation, **kwargs):
    start = time.perf_counter()
    outcome = "ok"
    try:
        return getattr(client(), operation)(**kwargs)
    except ClientError as e:
        outcome = e.response["Error"]["Code"]
        raise
    finally:
        DYNAMODB_LATENCY.labels(operation, outcome).observe(time.perf_counter() - start)


def error_code(e: ClientError) -> str:
    return e.response["Error"]["Code"]


def to_dynamo(values: dict) -> dict:
    return {k: _serializer.serialize(v) for k, v in values.items()}


def from_dynamo(item: dict) -> dict:
    out = {}
    for k, v in item.items():
        value = _deserializer.deserialize(v)
        if isinstance(value, Decimal):
            value = int(value) if value == value.to_integral_value() else float(value)
        out[k] = value
    return out
