#!/usr/bin/env python3
"""Deploy and exercise the audit Lambda (DynamoDB Streams -> Lambda) through the AWS SDK.

Usage: lambda_ctl.py deploy | test | logs [--minutes N] | destroy
"""

import argparse
import io
import json
import time
import uuid
import zipfile
from pathlib import Path

import common as c
from botocore.exceptions import ClientError

FUNCTION = "paas-audit"
ROLE = "paas-audit-lambda-role"
SRC = Path(__file__).parent / "functions" / "audit" / "handler.py"
LOG_GROUP = f"/aws/lambda/{FUNCTION}"
# Skip the global visit counter: it changes on every request and carries no audit value.
EVENT_FILTER = {
    "Filters": [
        {
            "Pattern": json.dumps(
                {"dynamodb": {"Keys": {"id": {"S": [{"anything-but": ["counter#visits"]}]}}}}
            )
        }
    ]
}


def package() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SRC, "handler.py")
    return buf.getvalue()


def ensure_stream(ddb) -> str:
    desc = ddb.describe_table(TableName=c.ITEMS_TABLE)["Table"]
    if desc.get("StreamSpecification", {}).get("StreamEnabled"):
        return desc["LatestStreamArn"]
    c.log(f"enabling NEW_AND_OLD_IMAGES stream on {c.ITEMS_TABLE}")
    res = ddb.update_table(
        TableName=c.ITEMS_TABLE,
        StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"},
    )
    return res["TableDescription"]["LatestStreamArn"]


def ensure_role(iam, stream_arn: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        iam.create_role(
            RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=[c.PROJECT_TAG]
        )
        c.log(f"+ role {ROLE}")
    except iam.exceptions.EntityAlreadyExistsException:
        c.log(f"= role {ROLE} already exists")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:DescribeStream",
                    "dynamodb:GetRecords",
                    "dynamodb:GetShardIterator",
                    "dynamodb:ListStreams",
                ],
                "Resource": stream_arn,
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{c.REGION}:{c.account_id()}:log-group:{LOG_GROUP}:*",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE, PolicyName="stream-and-logs", PolicyDocument=json.dumps(policy)
    )
    return iam.get_role(RoleName=ROLE)["Role"]["Arn"]


def deploy() -> None:
    ddb, iam, lam, logs = (c.client(s) for s in ("dynamodb", "iam", "lambda", "logs"))
    stream_arn = ensure_stream(ddb)
    role_arn = ensure_role(iam, stream_arn)
    try:  # Retain logs 7 days so the audit sink cannot outgrow the free tier.
        logs.create_log_group(logGroupName=LOG_GROUP, tags={"Project": "paas"})
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=7)

    code = package()
    for attempt in range(8):  # a new role needs a few seconds before Lambda can assume it
        try:
            lam.create_function(
                FunctionName=FUNCTION,
                Runtime="python3.12",
                Role=role_arn,
                Handler="handler.handler",
                Code={"ZipFile": code},
                MemorySize=128,
                Timeout=10,
                Architectures=["x86_64"],
                Tags={"Project": "paas"},
            )
            c.log(f"+ function {FUNCTION}")
            break
        except lam.exceptions.ResourceConflictException:
            lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
            c.log(f"~ function {FUNCTION} code updated")
            break
        except ClientError as e:
            if "cannot be assumed" not in e.response["Error"]["Message"] or attempt == 7:
                raise
            time.sleep(5)
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION)

    mappings = lam.list_event_source_mappings(FunctionName=FUNCTION)["EventSourceMappings"]
    if mappings:
        c.log(f"= event source mapping {mappings[0]['UUID']} already exists")
        return
    m = lam.create_event_source_mapping(
        FunctionName=FUNCTION,
        EventSourceArn=stream_arn,
        StartingPosition="LATEST",
        BatchSize=10,
        MaximumBatchingWindowInSeconds=1,
        MaximumRetryAttempts=2,
        BisectBatchOnFunctionError=True,
        FilterCriteria=EVENT_FILTER,
    )
    c.log(f"+ event source mapping {m['UUID']} ({c.ITEMS_TABLE} stream -> {FUNCTION})")


def test() -> None:
    """Generate real application events: create, update and delete an item, plus a counter hit."""
    ddb = c.client("dynamodb")
    item_id = f"audit-test-{uuid.uuid4().hex[:8]}"
    key = {"id": {"S": item_id}}
    c.log(f"put/update/delete {item_id} and bump counter#visits (the filter must drop it)")
    ddb.put_item(
        TableName=c.ITEMS_TABLE,
        Item={**key, "name": {"S": "lambda trigger test"}, "version": {"N": "1"}},
    )
    ddb.update_item(
        TableName=c.ITEMS_TABLE,
        Key=key,
        UpdateExpression="SET #n = :n, version = version + :one",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={":n": {"S": "renamed"}, ":one": {"N": "1"}},
    )
    ddb.update_item(
        TableName=c.ITEMS_TABLE,
        Key={"id": {"S": "counter#visits"}},
        UpdateExpression="ADD #c :one",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":one": {"N": "1"}},
    )
    ddb.delete_item(TableName=c.ITEMS_TABLE, Key=key)
    c.emit({"item_id": item_id, "next": "wait ~10 s, then run: lambda_ctl.py logs"})


def logs(minutes: int) -> None:
    cw = c.client("logs")
    start = int((time.time() - minutes * 60) * 1000)
    pages = cw.get_paginator("filter_log_events").paginate(logGroupName=LOG_GROUP, startTime=start)
    for page in pages:
        for e in page["events"]:
            ts = time.strftime("%H:%M:%S", time.gmtime(e["timestamp"] / 1000))
            print(f"{ts}Z {e['message'].rstrip()}")


def destroy() -> None:
    lam, iam = c.client("lambda"), c.client("iam")
    for m in lam.list_event_source_mappings(FunctionName=FUNCTION)["EventSourceMappings"]:
        lam.delete_event_source_mapping(UUID=m["UUID"])
        c.log(f"- event source mapping {m['UUID']}")
    try:
        lam.delete_function(FunctionName=FUNCTION)
        c.log(f"- function {FUNCTION}")
    except lam.exceptions.ResourceNotFoundException:
        pass
    try:
        iam.delete_role_policy(RoleName=ROLE, PolicyName="stream-and-logs")
        iam.delete_role(RoleName=ROLE)
        c.log(f"- role {ROLE}")
    except iam.exceptions.NoSuchEntityException:
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=["deploy", "test", "logs", "destroy"])
    p.add_argument("--minutes", type=int, default=10)
    args = p.parse_args()
    {
        "deploy": deploy,
        "test": test,
        "logs": lambda: logs(args.minutes),
        "destroy": destroy,
    }[args.action]()
