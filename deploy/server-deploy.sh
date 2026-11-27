#!/usr/bin/env bash
# Runs ON the server (sent by release.sh via Session Manager). Idempotent.
# Usage: server-deploy.sh <image-uri:tag> <secret-id> <region>
set -euo pipefail
IMAGE=$1 SECRET_ID=$2 REGION=$3
cd /srv/setuhaul
mountpoint -q /srv/setuhaul || { echo "data disk not mounted at /srv/setuhaul"; exit 1; }

# .env = non-secret config + secrets from Secrets Manager. Readable by root only, never logged.
umask 077
{
  cat config.env
  echo "SETUHAUL_IMAGE=$IMAGE"          # lets operators run plain `docker compose ps/logs` later
  aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
      --query SecretString --output text \
    | python3 -c 'import json,sys; [print(f"{k}={v}") for k, v in json.load(sys.stdin).items()]'
} > .env.new
mv .env.new .env

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${IMAGE%%/*}" >/dev/null
docker compose pull --quiet

# First deploy only: load the demo scenario into the empty data disk.
if [ ! -f data/app.db ]; then
  docker compose run --rm api python scripts/load_scenario.py two_drivers_one_slot
fi

docker compose up -d --remove-orphans
docker image prune -f >/dev/null
echo "deployed $IMAGE"
