#!/usr/bin/env python3
"""Spawn, inspect and terminate EC2 burst nodes through the AWS SDK (no console).

Usage:
  node.py up [--count N] [--tag TAG] [--type t3.micro] [--no-wait-healthy]
  node.py list
  node.py down (--all | INSTANCE_ID ...)

stdout is JSON (consumed by the Phase 3 controller); progress goes to stderr.
"""

import argparse
import time
import urllib.error
import urllib.request

import common as c
from botocore.exceptions import ClientError

AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
LIVE_STATES = ["pending", "running", "stopping", "stopped"]


def user_data(tag: str) -> str:
    # The node uses its instance profile for DynamoDB: no keys on disk, and AWS_ENDPOINT_URL
    # stays unset so boto3 talks to real AWS. IMDSv2 hop limit 2 lets the container reach it.
    return f"""#!/bin/bash
set -euxo pipefail
dnf install -y docker
systemctl enable --now docker
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token \\
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \\
  http://169.254.169.254/latest/meta-data/instance-id)
docker run -d --name paas-api --restart unless-stopped --hostname "aws-$IID" \\
  --stop-timeout 30 -p {c.API_PORT}:8000 \\
  -e AWS_REGION={c.REGION} -e ITEMS_TABLE={c.ITEMS_TABLE} -e SESSIONS_TABLE={c.SESSIONS_TABLE} \\
  -e APP_VERSION={tag} {c.IMAGE}:{tag}
"""


def node_filter(states: list[str]) -> list[dict]:
    return [
        {"Name": "tag:Project", "Values": [c.PROJECT_TAG["Value"]]},
        {"Name": "tag:ManagedBy", "Values": [c.MANAGED_TAG["Value"]]},
        {"Name": "tag:Role", "Values": ["burst-node"]},
        {"Name": "instance-state-name", "Values": states},
    ]


def describe(ec2, states: list[str], ids: list[str] | None = None) -> list[dict]:
    kwargs = {"Filters": node_filter(states)}
    if ids:
        kwargs["InstanceIds"] = ids
    out = []
    for page in ec2.get_paginator("describe_instances").paginate(**kwargs):
        for r in page["Reservations"]:
            out += [summarize(i) for i in r["Instances"]]
    return out


def summarize(i: dict) -> dict:
    return {
        "instance_id": i["InstanceId"],
        "state": i["State"]["Name"],
        "type": i["InstanceType"],
        "public_ip": i.get("PublicIpAddress"),
        "private_ip": i.get("PrivateIpAddress"),
        "launched_at": i["LaunchTime"].isoformat(),
    }


def wait_healthy(ip: str, timeout: int) -> bool:
    url = f"http://{ip}:{c.API_PORT}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass  # still booting or pulling the image
        time.sleep(5)
    return False


def run_instances(ec2, **kwargs) -> dict:
    # A freshly created instance profile takes a few seconds to become usable by EC2.
    for attempt in range(6):
        try:
            return ec2.run_instances(**kwargs)
        except ClientError as e:
            msg = e.response["Error"]["Message"]
            if "Invalid IAM Instance Profile" not in msg or attempt == 5:
                raise
            time.sleep(5)


def up(count: int, tag: str, itype: str, healthy: bool, timeout: int) -> list[dict]:
    if itype not in c.ALLOWED_TYPES:
        allowed = ", ".join(c.ALLOWED_TYPES)
        raise SystemExit(f"{itype} is not Free Tier eligible; allowed: {allowed}")
    ec2, ssm = c.client("ec2"), c.client("ssm")
    sgs = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [c.SG_NAME]}])
    if not sgs["SecurityGroups"]:
        raise SystemExit("Security group missing: run bootstrap.py up first.")
    ami = ssm.get_parameter(Name=AMI_PARAM)["Parameter"]["Value"]
    tags = [c.PROJECT_TAG, c.MANAGED_TAG, {"Key": "Role", "Value": "burst-node"}]

    t0 = time.monotonic()
    c.log(f"RunInstances: {count} x {itype} ({ami}), image tag {tag}")
    res = run_instances(
        ec2,
        ImageId=ami,
        InstanceType=itype,
        MinCount=count,
        MaxCount=count,
        SecurityGroupIds=[sgs["SecurityGroups"][0]["GroupId"]],
        IamInstanceProfile={"Name": c.NODE_PROFILE},
        UserData=user_data(tag),
        MetadataOptions={"HttpTokens": "required", "HttpPutResponseHopLimit": 2},
        InstanceInitiatedShutdownBehavior="terminate",
        TagSpecifications=[
            {"ResourceType": rt, "Tags": tags + [{"Key": "Name", "Value": "paas-burst-node"}]}
            for rt in ("instance", "volume")
        ],
    )
    ids = [i["InstanceId"] for i in res["Instances"]]
    c.log(f"created {', '.join(ids)}; waiting for running state")
    ec2.get_waiter("instance_running").wait(InstanceIds=ids)
    t_running = time.monotonic() - t0

    nodes = describe(ec2, ["running"], ids)
    for n in nodes:
        n["seconds_to_running"] = round(t_running, 1)
        c.log(f"{n['instance_id']} running, public IP {n['public_ip']}")
    if healthy:
        for n in nodes:
            ok = wait_healthy(n["public_ip"], timeout)
            n["healthy"] = ok
            n["seconds_to_healthy"] = round(time.monotonic() - t0, 1) if ok else None
            c.log(f"{n['instance_id']} /health {'200 OK' if ok else 'TIMEOUT'}")
    return nodes


def down(ids: list[str], all_nodes: bool) -> list[dict]:
    ec2 = c.client("ec2")
    # Filtering by tag means the script can never terminate an instance it did not create.
    targets = describe(ec2, LIVE_STATES, None if all_nodes else ids)
    victims = [n["instance_id"] for n in targets]
    if not victims:
        c.log("no burst nodes to terminate")
        return []
    t0 = time.monotonic()
    c.log(f"TerminateInstances: {', '.join(victims)}")
    ec2.terminate_instances(InstanceIds=victims)
    ec2.get_waiter("instance_terminated").wait(InstanceIds=victims)
    elapsed = round(time.monotonic() - t0, 1)
    c.log(f"terminated in {elapsed}s")
    return [
        {"instance_id": v, "state": "terminated", "seconds_to_terminated": elapsed} for v in victims
    ]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--count", type=int, default=1)
    u.add_argument("--tag", default="latest", help="API image tag on GHCR")
    u.add_argument("--type", default="t3.micro")
    u.add_argument("--no-wait-healthy", action="store_true")
    u.add_argument("--timeout", type=int, default=300, help="seconds to wait for /health")
    sub.add_parser("list")
    d = sub.add_parser("down")
    d.add_argument("ids", nargs="*")
    d.add_argument("--all", action="store_true")
    args = p.parse_args()

    if args.cmd == "up":
        c.emit(up(args.count, args.tag, args.type, not args.no_wait_healthy, args.timeout))
    elif args.cmd == "list":
        c.emit(describe(c.client("ec2"), LIVE_STATES))
    else:
        if not args.ids and not args.all:
            p.error("pass instance ids or --all")
        c.emit(down(args.ids, args.all))
