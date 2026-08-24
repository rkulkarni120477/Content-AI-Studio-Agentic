"""Allow this machine's current public IP to reach the DIS structure-store RDS.

Why this exists
---------------
``dis-dev-postgres`` (us-east-1) is publicly accessible but its security group
``dis-rds-sg`` admits a single hard-coded /32. When the machine's public IP
changes — an overnight ISP lease rotation is enough — DIS can no longer open the
Postgres connection that ``enumerate_block`` needs, and block-wide CDD/Blueprint
generation fails with the generic "no enumerated days or DIS error" two layers
away from the real cause.

Run it when generation starts failing with that message:

    .venv/bin/python scripts/allow_my_ip_dis_rds.py            # add the rule
    .venv/bin/python scripts/allow_my_ip_dis_rds.py --check    # look, change nothing

Idempotent: an existing rule for the same IP is reported, not duplicated.

This is a stop-gap. The durable fix is to stop pinning a dynamic address —
put DIS and the RDS in one VPC, or reach it through an SSM port-forward, and set
DIS_STRUCTURE_STORE_URL accordingly (see dis_backend/config/settings.py).
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

GROUP_ID = "sg-04fee7eb6501c9adc"   # dis-rds-sg
REGION = "us-east-1"
PORT = 5432


def public_ip() -> str:
    return urllib.request.urlopen(
        "https://checkip.amazonaws.com", timeout=10).read().decode().strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="show the current rules and this machine's IP, then exit")
    args = ap.parse_args()

    try:
        import boto3
        from dotenv import load_dotenv
    except ImportError as exc:
        print(f"missing dependency: {exc}. Run this with the project venv:")
        print("  .venv/bin/python scripts/allow_my_ip_dis_rds.py")
        return 2

    # Credentials come from the gitignored env files, same as the services use.
    load_dotenv("dis_backend/.env")
    load_dotenv(".env")
    key, secret = os.getenv("AWS_ACCESS_KEY_ID"), os.getenv("AWS_SECRET_ACCESS_KEY")
    if not key or not secret:
        print("no AWS credentials found in dis_backend/.env or .env")
        return 2

    ec2 = boto3.client("ec2", region_name=REGION,
                       aws_access_key_id=key, aws_secret_access_key=secret)

    ip = public_ip()
    cidr = f"{ip}/32"
    print(f"this machine's public IP : {ip}")

    try:
        group = ec2.describe_security_groups(GroupIds=[GROUP_ID])["SecurityGroups"][0]
    except Exception as exc:
        print(f"could not read {GROUP_ID}: {type(exc).__name__}: {exc}")
        return 1

    allowed = [
        r["CidrIp"]
        for perm in group["IpPermissions"]
        if perm.get("FromPort") == PORT
        for r in perm.get("IpRanges", [])
    ]
    print(f"{GROUP_ID} currently allows {PORT}/tcp from: {allowed or '(nothing)'}")

    if cidr in allowed:
        print(f"\nalready allowed — {cidr} is present. If generation still fails, the "
              f"cause is elsewhere; check the DIS container log for OperationalError.")
        return 0
    if args.check:
        print(f"\n--check: would add {cidr}. Re-run without --check to apply.")
        return 0

    try:
        ec2.authorize_security_group_ingress(
            GroupId=GROUP_ID,
            IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": PORT, "ToPort": PORT,
                "IpRanges": [{"CidrIp": cidr, "Description": "dev access (DIS structure store)"}],
            }],
        )
    except Exception as exc:
        name = type(exc).__name__
        if "Duplicate" in str(exc):
            print(f"\nalready allowed ({name}).")
            return 0
        print(f"\nfailed to add the rule: {name}: {exc}")
        print("If this is an authorization error, add the rule in the console:")
        print(f"  EC2 -> Security Groups -> {GROUP_ID} -> Inbound rules -> "
              f"PostgreSQL {PORT} -> My IP")
        return 1

    print(f"\nADDED {cidr} to {GROUP_ID} on {PORT}/tcp.")
    print("Retry the generation. Remove it later with:")
    print(f"  ec2.revoke_security_group_ingress(GroupId='{GROUP_ID}', ...) or via the console.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
