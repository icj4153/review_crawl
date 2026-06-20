#!/bin/sh
set -eu

APP_DIR=${APP_DIR:-/volume1/docker/review_crawl}
KEY_DIR=${KEY_DIR:-/volume1/docker/review_crawl_secrets}
DOCKER=${DOCKER:-/usr/local/bin/docker}
COMPOSE=${COMPOSE:-/usr/local/bin/docker-compose}
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

cd "$APP_DIR"

if [ -f "$KEY_DIR/github_deploy_key" ]; then
  "$DOCKER" run --rm \
    -v "$APP_DIR:/repo" \
    -v "$KEY_DIR:/root/.ssh:ro" \
    alpine/git \
    -C /repo \
    -c safe.directory=/repo \
    -c core.sshCommand="ssh -i /root/.ssh/github_deploy_key -o StrictHostKeyChecking=no" \
    pull --ff-only
else
  "$DOCKER" run --rm \
    -v "$APP_DIR:/repo" \
    alpine/git \
    -C /repo \
    -c safe.directory=/repo \
    pull --ff-only
fi

mkdir -p output

"$DOCKER" rm -f naver-review-web 2>/dev/null || true
wait_count=0
while "$DOCKER" ps -a --filter name=naver-review-web --format '{{.Names}}' | grep -q '^naver-review-web$'; do
  if [ "$wait_count" -ge 30 ]; then
    echo "Timed out waiting for naver-review-web removal"
    exit 1
  fi
  wait_count=$((wait_count + 1))
  sleep 1
done

"$COMPOSE" up -d --build
