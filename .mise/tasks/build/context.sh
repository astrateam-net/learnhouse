#!/usr/bin/env bash
#MISE description="Assemble the docker build context: the patched tree"
#MISE dir="{{config_root}}"
#MISE depends=["up"]
set -Eeuo pipefail

# Upstream's Dockerfile builds straight from the tree; nothing beyond the series and overlay is
# needed. The task exists so CI and `mise run build` share one entry point.
echo "build context: lib/learnhouse at $(git -C lib/learnhouse rev-parse --short HEAD)"
