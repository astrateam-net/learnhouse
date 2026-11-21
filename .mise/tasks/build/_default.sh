#!/usr/bin/env bash
#MISE alias="build"
#MISE description="Build the image of the assembled tree and run its structure tests"
#MISE dir="{{config_root}}"
#MISE depends=["build:context"]
#USAGE arg "[patch]" default="0" help="Our release slot: the N in 4.5.N"
set -Eeuo pipefail

# shellcheck source=../../lib.sh
source .mise/lib.sh

main() {
  VERSION="$(our_version "${usage_patch:-0}")"
  BUILD_ID="$(learnhouse_commit)"
  export VERSION BUILD_ID
  echo "learnhouse ${VERSION} (LearnHouse $(learnhouse_version) at ${BUILD_ID:0:9})"

  mkdir -p .cache
  docker buildx bake --metadata-file .cache/docker-bake.json image-local

  # bake targets carry no tags, so the fresh image is addressed by digest; the containerd image
  # store reports it as containerimage.digest, the classic store as containerimage.config.digest.
  local image
  image="$(jq -r '."image-local" | ."containerimage.digest" // ."containerimage.config.digest"' .cache/docker-bake.json)"
  docker tag "$image" "learnhouse:${VERSION}"
  container-structure-test test --image "learnhouse:${VERSION}" --config tests.yaml
}

main "$@"
