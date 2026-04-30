#!/usr/bin/env bash
# Renders package/sinas-grove.yaml with GROVE_URL substituted in.
# Usage:
#   GROVE_URL=https://grove.example.com ./scripts/render-package.sh | sinas package install -
set -euo pipefail

: "${GROVE_URL:?GROVE_URL must be set (e.g. http://host.docker.internal:8080 for local docker-compose)}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
sed "s|__GROVE_URL__|${GROVE_URL}|g" "${HERE}/package/sinas-grove.yaml"
