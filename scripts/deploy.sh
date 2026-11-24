#!/usr/bin/env bash
# Usage: deploy.sh [tag]. "local" builds the images here; any other tag is pulled from GHCR.
# Services are replaced one at a time (rolling update); SERVICES limits which ones.
set -euo pipefail

tag=${1:-local}
services=${SERVICES:-api-1 api-2 web}
export API_TAG=$tag WEB_TAG=$tag

if [[ "$tag" == "local" ]]; then
  docker compose build api-1 web
else
  docker compose pull api-1 api-2 web
fi

for svc in $services; do
  echo "updating $svc to $tag"
  docker compose up -d --no-deps --no-build --wait "$svc"
  # Give nginx time to re-resolve the new container and forget past failures before touching the next one.
  sleep 10
done

put_param() {
  aws ssm put-parameter --name "$1" --value "$2" --type String --overwrite >/dev/null
  echo "ssm $1 = $2"
}

put_param /paas/api/version "$API_TAG"
put_param /paas/web/version "$WEB_TAG"
put_param /paas/deployed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
