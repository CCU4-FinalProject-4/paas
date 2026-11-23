#!/usr/bin/env bash
set -euo pipefail

: "${ITEMS_TABLE:?ITEMS_TABLE is not set}"
: "${SESSIONS_TABLE:?SESSIONS_TABLE is not set}"

# PROVISIONED 5/5 is covered by the AWS free tier; PAY_PER_REQUEST is not.
create_table() {
  local table=$1 key=$2
  if aws dynamodb describe-table --table-name "$table" >/dev/null 2>&1; then
    echo "= $table already exists"
  else
    aws dynamodb create-table \
      --table-name "$table" \
      --attribute-definitions "AttributeName=$key,AttributeType=S" \
      --key-schema "AttributeName=$key,KeyType=HASH" \
      --billing-mode PROVISIONED \
      --provisioned-throughput ReadCapacityUnits=5,WriteCapacityUnits=5 \
      --table-class STANDARD >/dev/null
    echo "+ $table created"
  fi
  aws dynamodb wait table-exists --table-name "$table"
}

enable_ttl() {
  local table=$1 attr=$2 status
  status=$(aws dynamodb describe-time-to-live --table-name "$table" \
    --query 'TimeToLiveDescription.TimeToLiveStatus' --output text)
  if [[ "$status" == "ENABLED" || "$status" == "ENABLING" ]]; then
    echo "= $table TTL already enabled"
  else
    aws dynamodb update-time-to-live --table-name "$table" \
      --time-to-live-specification "Enabled=true,AttributeName=$attr" >/dev/null
    echo "+ $table TTL enabled on $attr"
  fi
}

create_table "$ITEMS_TABLE" id
create_table "$SESSIONS_TABLE" session_id
enable_ttl "$SESSIONS_TABLE" expires_at
