#!/bin/sh
set -eu

APP_DIR=${APP_DIR:-/volume1/docker/review_crawl}
KEY_DIR=${KEY_DIR:-/volume1/docker/review_crawl_secrets}
DOCKER=${DOCKER:-/usr/local/bin/docker}
COMPOSE=${COMPOSE:-/usr/local/bin/docker-compose}
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

cd "$APP_DIR"

"$DOCKER" run --rm \
  -v "$APP_DIR:/repo" \
  -v "$KEY_DIR:/root/.ssh:ro" \
  alpine/git \
  -C /repo \
  -c safe.directory=/repo \
  -c core.sshCommand="ssh -i /root/.ssh/github_deploy_key -o StrictHostKeyChecking=no" \
  pull --ff-only

"$COMPOSE" up -d --build
