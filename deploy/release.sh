#!/usr/bin/env bash
# Run ON YOUR LAPTOP from the repo root:  deploy/release.sh dev
# Builds the image for the current commit, pushes it, and deploys it to the server.
set -euo pipefail
ENV_NAME=${1:?usage: deploy/release.sh dev|staging|prod}
REGION=ap-southeast-2
STACK=SetuhaulHosting-$ENV_NAME

if [ -n "$(git status --porcelain -- src ui scripts scenarios data/seed pyproject.toml uv.lock Dockerfile README.md)" ]; then
  echo "Commit your changes first: the image tag must describe the exact code."; exit 1
fi
TAG=$(git rev-parse --short HEAD)
out() { aws cloudformation describe-stacks --region $REGION --stack-name $STACK \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
REPO=$(out ImageRepoUri); SERVER=$(out ServerId); SECRET=$(out SecretArn); URL=$(out AppUrl)

# 1. Image: build once per commit (tags are immutable)
if ! aws ecr describe-images --region $REGION --repository-name "${REPO##*/}" --image-ids imageTag=$TAG >/dev/null 2>&1; then
  aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
  docker build --pull --provenance=false --platform linux/arm64 -t "$REPO:$TAG" .
  docker push "$REPO:$TAG"
fi

# 2. Ship compose + config + script to the server and run it (Session Manager, no SSH)
b64() { base64 < "$1" | tr -d '\n'; }
CMD_ID=$(aws ssm send-command --region $REGION --instance-ids "$SERVER" \
  --document-name AWS-RunShellScript --comment "setuhaul $ENV_NAME $TAG" \
  --parameters "commands=[
    \"echo $(b64 deploy/compose.yaml) | base64 -d > /srv/setuhaul/compose.yaml\",
    \"echo $(b64 deploy/$ENV_NAME.env) | base64 -d > /srv/setuhaul/config.env\",
    \"echo $(b64 deploy/server-deploy.sh) | base64 -d > /srv/setuhaul/server-deploy.sh\",
    \"bash /srv/setuhaul/server-deploy.sh $REPO:$TAG $SECRET $REGION\"
  ]" --query Command.CommandId --output text)
aws ssm wait command-executed --region $REGION --command-id "$CMD_ID" --instance-id "$SERVER" || true
aws ssm get-command-invocation --region $REGION --command-id "$CMD_ID" --instance-id "$SERVER" \
  --query '[Status, StandardOutputContent, StandardErrorContent]' --output text | tail -20
echo "App: $URL"
