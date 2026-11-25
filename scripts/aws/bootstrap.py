#!/usr/bin/env python3
"""One-time AWS plumbing for the burst nodes: security group, IAM role and instance profile.

Usage: bootstrap.py up | down | status   (idempotent; no console needed)
"""

import argparse
import json
import urllib.request

import common as c
from botocore.exceptions import ClientError

NODE_POLICY_NAME = "paas-dynamodb-access"


def detect_cidr() -> str:
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
        return r.read().decode().strip() + "/32"


def node_policy() -> dict:
    # The API only calls these six operations; scope them to the two tables, nothing else.
    tables = [c.table_arn(c.ITEMS_TABLE), c.table_arn(c.SESSIONS_TABLE)]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:DescribeTable",
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Scan",
                ],
                "Resource": tables,
            }
        ],
    }


def default_vpc_id(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise SystemExit("No default VPC in this region; create one or pass a custom VPC.")
    return vpcs[0]["VpcId"]


def find_sg(ec2):
    res = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [c.SG_NAME]}])
    return res["SecurityGroups"][0] if res["SecurityGroups"] else None


def ensure_sg(ec2, cidr: str) -> str:
    sg = find_sg(ec2)
    if sg:
        c.log(f"= security group {sg['GroupId']} already exists")
        return sg["GroupId"]
    sg_id = ec2.create_security_group(
        GroupName=c.SG_NAME,
        Description="paas burst nodes: API port from the controller host only",
        VpcId=default_vpc_id(ec2),
        TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [c.PROJECT_TAG, c.MANAGED_TAG]}
        ],
    )["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": c.API_PORT,
                "ToPort": c.API_PORT,
                "IpRanges": [{"CidrIp": cidr, "Description": "Nginx on the local workstation"}],
            }
        ],
    )
    c.log(f"+ security group {sg_id} (tcp/{c.API_PORT} from {cidr})")
    return sg_id


def ensure_role(iam) -> None:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        iam.create_role(
            RoleName=c.NODE_ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Tags=[c.PROJECT_TAG, c.MANAGED_TAG],
        )
        c.log(f"+ role {c.NODE_ROLE}")
    except iam.exceptions.EntityAlreadyExistsException:
        c.log(f"= role {c.NODE_ROLE} already exists")
    # put_role_policy overwrites, so re-running keeps the policy in sync with the code.
    iam.put_role_policy(
        RoleName=c.NODE_ROLE,
        PolicyName=NODE_POLICY_NAME,
        PolicyDocument=json.dumps(node_policy()),
    )
    try:
        iam.create_instance_profile(InstanceProfileName=c.NODE_PROFILE)
        iam.add_role_to_instance_profile(InstanceProfileName=c.NODE_PROFILE, RoleName=c.NODE_ROLE)
        c.log(f"+ instance profile {c.NODE_PROFILE}")
    except iam.exceptions.EntityAlreadyExistsException:
        c.log(f"= instance profile {c.NODE_PROFILE} already exists")


def up(cidr: str | None) -> None:
    ec2, iam = c.client("ec2"), c.client("iam")
    sg_id = ensure_sg(ec2, cidr or detect_cidr())
    ensure_role(iam)
    c.emit({"security_group": sg_id, "role": c.NODE_ROLE, "instance_profile": c.NODE_PROFILE})


def down() -> None:
    ec2, iam = c.client("ec2"), c.client("iam")
    try:
        iam.remove_role_from_instance_profile(
            InstanceProfileName=c.NODE_PROFILE, RoleName=c.NODE_ROLE
        )
        iam.delete_instance_profile(InstanceProfileName=c.NODE_PROFILE)
        iam.delete_role_policy(RoleName=c.NODE_ROLE, PolicyName=NODE_POLICY_NAME)
        iam.delete_role(RoleName=c.NODE_ROLE)
        c.log("- role and instance profile deleted")
    except iam.exceptions.NoSuchEntityException:
        c.log("= role and instance profile already gone")
    sg = find_sg(ec2)
    if sg:
        try:
            ec2.delete_security_group(GroupId=sg["GroupId"])
            c.log("- security group deleted")
        except ClientError as e:
            c.log(f"! security group kept: {e.response['Error']['Message']}")


def status() -> None:
    ec2, iam = c.client("ec2"), c.client("iam")
    sg = find_sg(ec2)
    try:
        iam.get_instance_profile(InstanceProfileName=c.NODE_PROFILE)
        profile = True
    except iam.exceptions.NoSuchEntityException:
        profile = False
    c.emit({"security_group": sg["GroupId"] if sg else None, "instance_profile": profile})


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=["up", "down", "status"])
    p.add_argument("--cidr", help="source CIDR for the API port (default: this host's public IP)")
    args = p.parse_args()
    {"up": lambda: up(args.cidr), "down": down, "status": status}[args.action]()
