"""Shared settings and helpers for the Phase 2 scripts (EC2 burst nodes and the audit Lambda)."""

import json
import os
import sys
import time

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
ITEMS_TABLE = os.environ.get("ITEMS_TABLE", "paas-items")
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "paas-sessions")

PROJECT_TAG = {"Key": "Project", "Value": "paas"}
MANAGED_TAG = {"Key": "ManagedBy", "Value": "paas-scripts"}

# Only Free Tier eligible sizes; the IAM policy in policies/ enforces the same list server-side.
ALLOWED_TYPES = ("t2.micro", "t3.micro")

SG_NAME = "paas-burst-sg"
NODE_ROLE = "paas-burst-node-role"
NODE_PROFILE = "paas-burst-node-profile"
API_PORT = 8000
IMAGE = "ghcr.io/ccu4-finalproject-4/api"


def log(msg: str) -> None:
    # Progress goes to stderr so stdout stays machine-readable for the Phase 3 controller.
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def emit(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def client(service: str):
    # With AWS_ENDPOINT_URL set (the Floci profile) boto3 would silently talk to the emulator.
    if os.environ.get("AWS_ENDPOINT_URL") and not os.environ.get("PAAS_ALLOW_ENDPOINT"):
        sys.exit("AWS_ENDPOINT_URL is set: run with ENV_FILE=.env.aws so the calls reach real AWS.")
    return boto3.client(service, region_name=REGION)


def account_id() -> str:
    return client("sts").get_caller_identity()["Account"]


def table_arn(table: str) -> str:
    return f"arn:aws:dynamodb:{REGION}:{account_id()}:table/{table}"
