#!/bin/bash
# Seeds the simulated BrewCart AWS account with a realistic mess.
# Runs automatically inside LocalStack once it is ready.
set -euo pipefail
R=us-east-1
aws_() { awslocal --region "$R" "$@"; }
IMG=$(aws_ ec2 describe-images --query 'Images[0].ImageId' --output text)
run() { aws_ ec2 run-instances --image-id "$IMG" --instance-type t3.micro --count 1 "$@" --query 'Instances[0].InstanceId' --output text; }

# --- instances ---
run --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=brewcart-web-1},{Key=Owner,Value=platform},{Key=Environment,Value=prod}]' >/dev/null
run --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=brewcart-api-1},{Key=Owner,Value=platform},{Key=Environment,Value=prod}]' >/dev/null
JENKINS=$(run --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=old-jenkins},{Key=Owner,Value=platform}]')
TESTBOX=$(run --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=test-box-arjun}]')
run >/dev/null   # no tags at all
aws_ ec2 stop-instances --instance-ids "$JENKINS" "$TESTBOX" >/dev/null

# --- unattached volumes ---
vol() { aws_ ec2 create-volume --availability-zone ${R}a --size "$1" --volume-type gp3 "${@:2}" --query VolumeId --output text; }
BACKUP=$(vol 50 --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=brewcart-mongo-backup-2025},{Key=Owner,Value=platform}]')
vol 100 >/dev/null
vol 20 >/dev/null
vol 500 --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=analytics-restore-staging},{Key=Owner,Value=data-team},{Key=Purpose,Value=weekly-restore-test}]' >/dev/null

# --- snapshots ---
aws_ ec2 create-snapshot --volume-id "$BACKUP" --description "mongo backup 2025" --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Owner,Value=platform}]' >/dev/null
aws_ ec2 create-snapshot --volume-id "$BACKUP" --description "manual snapshot, no owner" >/dev/null

echo "BrewCart AWS mess seeded: 5 instances (2 stopped), 4 unattached volumes, 2 snapshots"
