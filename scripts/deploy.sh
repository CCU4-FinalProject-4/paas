#!/usr/bin/env bash
# Usage: deploy.sh [tag]. "local" builds the images here; any other tag is pulled from GHCR.
set -euo pipefail

tag=${1:-local}
export API_TAG=$tag WEB_TAG=$tag

if [[ "$tag" == "local" ]]; then
  docker compose build api-1 web
else
  docker compose pull api-1 api-2 web
fi

docker compose up -d --no-build --wait api-1 api-2 web

put_param() {
  aws ssm put-parameter --name "$1" --value "$2" --type String --overwrite >/dev/null
  echo "ssm $1 = $2"
}

put_param /paas/api/version "$API_TAG"
put_param /paas/web/version "$WEB_TAG"
put_param /paas/deployed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
