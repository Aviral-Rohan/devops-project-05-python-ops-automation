#!/usr/bin/env python3
"""Find unused or untagged EC2 resources. Report-only by default; deletes only with --apply."""
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError, NoCredentialsError

log = logging.getLogger("cloud_cleanup")

REQUIRED_TAG = "Owner"


def tags_of(resource):
    return {t["Key"]: t["Value"] for t in resource.get("Tags", [])}


def unattached_volumes(ec2):
    pages = ec2.get_paginator("describe_volumes").paginate(Filters=[{"Name": "status", "Values": ["available"]}])
    return [v for page in pages for v in page["Volumes"]]


def live_instances(ec2):
    states = ["pending", "running", "stopping", "stopped"]
    pages = ec2.get_paginator("describe_instances").paginate(Filters=[{"Name": "instance-state-name", "Values": states}])
    return [i for page in pages for r in page["Reservations"] for i in r["Instances"]]


def old_snapshots(ec2, days, account):
    """Our own snapshots older than `days`. OwnerId is checked as well: never trust a filter blindly."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pages = ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"])
    return [s for page in pages for s in page["Snapshots"] if s["OwnerId"] == account and s["StartTime"] < cutoff]


def finding(rtype, rid, tags, detail, issues, action):
    return {"type": rtype, "id": rid, "name": tags.get("Name", "-"), "owner": tags.get(REQUIRED_TAG, "-"),
            "detail": detail, "issues": issues, "tags": tags, "action": action}


def build_report(ec2, snapshot_days, account):
    """One row per resource: what is wrong with it and what this tool would do."""
    findings = []
    for v in unattached_volumes(ec2):
        t = tags_of(v)
        issues = ["unattached"] + ([f"no {REQUIRED_TAG}"] if REQUIRED_TAG not in t else [])
        findings.append(finding("volume", v["VolumeId"], t, f'{v["Size"]} GiB', issues, "delete"))
    for s in old_snapshots(ec2, snapshot_days, account):
        t = tags_of(s)
        issues = [f"older than {snapshot_days}d"] + ([f"no {REQUIRED_TAG}"] if REQUIRED_TAG not in t else [])
        findings.append(finding("snapshot", s["SnapshotId"], t, f'{s["StartTime"]:%Y-%m-%d}', issues, "delete"))
    for i in live_instances(ec2):
        t = tags_of(i)
        issues = (["stopped"] if i["State"]["Name"] == "stopped" else []) + ([f"no {REQUIRED_TAG}"] if REQUIRED_TAG not in t else [])
        if issues:
            findings.append(finding("instance", i["InstanceId"], t, i["InstanceType"], issues, "review"))
    return findings


def print_report(findings):
    if not findings:
        print("Nothing to clean up.")
        return
    print(f"{'TYPE':<9} {'ID':<22} {'NAME':<27} {'OWNER':<10} {'DETAIL':<11} {'ISSUES':<26} ACTION")
    for f in findings:
        print(f"{f['type']:<9} {f['id']:<22} {f['name'][:26]:<27} {f['owner'][:9]:<10} {f['detail']:<11} "
              f"{', '.join(f['issues']):<26} {f['action']}")
    deletable = sum(1 for f in findings if f["action"] == "delete")
    print(f"\n{len(findings)} finding(s): {deletable} to delete, "
          f"{len(findings) - deletable} for review (instances are never deleted by this tool)")


def apply(ec2, findings):
    deleted = failed = 0
    for f in findings:
        if f["action"] != "delete":
            continue
        try:
            if f["type"] == "volume":
                ec2.delete_volume(VolumeId=f["id"])
            elif f["type"] == "snapshot":
                ec2.delete_snapshot(SnapshotId=f["id"])
            log.info("Deleted %s %s (%s)", f["type"], f["id"], f["name"])
            deleted += 1
        except ClientError as err:
            log.error("Could not delete %s %s: %s", f["type"], f["id"], err.response["Error"]["Message"])
            failed += 1
    log.info("Deleted %d resource(s), %d failed", deleted, failed)
    return failed


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Report (and optionally delete) unused EC2 resources.")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--endpoint-url", help="e.g. http://localhost:4566 for LocalStack")
    p.add_argument("--snapshot-age-days", type=int, default=30, help="snapshots older than this are 'old' (default 30)")
    p.add_argument("--apply", action="store_true", help="actually delete unattached volumes and old snapshots")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    ec2 = boto3.client("ec2", region_name=args.region, endpoint_url=args.endpoint_url)
    sts = boto3.client("sts", region_name=args.region, endpoint_url=args.endpoint_url)
    try:
        account = sts.get_caller_identity()["Account"]
        log.info("Account %s, region %s%s", account, args.region, " (APPLY MODE)" if args.apply else " (report only)")
        findings = build_report(ec2, args.snapshot_age_days, account)
        print_report(findings)
        if not args.apply:
            log.info("Report only. Nothing was changed. Re-run with --apply to delete the 'delete' rows.")
            return 0
        return 1 if apply(ec2, findings) else 0
    except EndpointConnectionError as err:
        log.error("Cannot reach the AWS endpoint (%s). Is LocalStack running?", err.kwargs.get("endpoint_url"))
    except NoCredentialsError:
        log.error("No AWS credentials. For LocalStack: export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test")
    except ClientError as err:
        log.error("AWS API error: %s", err.response["Error"]["Message"])
    except BotoCoreError as err:
        log.error("AWS SDK error: %s", err)
    return 2


if __name__ == "__main__":
    sys.exit(main())