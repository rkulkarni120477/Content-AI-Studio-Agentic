#!/bin/bash
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
E=http://localhost:4566

aws --endpoint-url=$E s3 mb s3://dis-raw-aim 2>/dev/null
aws --endpoint-url=$E s3 mb s3://dis-processed-aim 2>/dev/null
aws --endpoint-url=$E s3 mb s3://dis-raw-default 2>/dev/null

# Actual bucket used by the current S3-only Source Library storage provider
# (storage/provider.py, DIS_S3_BUCKET in dis_backend/.env) — the buckets above
# are stale/unused names from an earlier storage layout, kept as harmless
# no-ops in case anything still references them.
aws --endpoint-url=$E s3 mb s3://content-ai-studio-local-test 2>/dev/null

aws --endpoint-url=$E sqs create-queue --queue-name dis-queue
aws --endpoint-url=$E sqs create-queue --queue-name dis-dlq

aws --endpoint-url=$E dynamodb create-table --table-name dis-token-usage \
  --attribute-definitions AttributeName=user_id,AttributeType=S AttributeName=date,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH AttributeName=date,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST 2>/dev/null || true

aws --endpoint-url=$E dynamodb create-table --table-name dis-fingerprints \
  --attribute-definitions AttributeName=tenant_id,AttributeType=S AttributeName=fp,AttributeType=S \
  --key-schema AttributeName=tenant_id,KeyType=HASH AttributeName=fp,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST 2>/dev/null || true

echo "LocalStack ready."
