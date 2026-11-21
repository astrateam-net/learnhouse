#!/usr/bin/env bash
set -euo pipefail

# git hands a hook an absolute GIT_DIR, and in a linked worktree that makes
# `git -C lib/learnhouse` read the outer index. Every call here names its repo.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY

pushd() {
  builtin pushd "$@" > /dev/null
}

popd() {
  builtin popd > /dev/null
}

learnhouse_version() {
  jq -r .version lib/learnhouse/apps/web/package.json
}

# LearnHouse 1.4.5 releases as 4.5.N: the major is dropped and the last slot is ours ($1), so one
# upstream version can carry several releases of the series.
# Not `git describe`: actions/checkout fetches submodules shallow and untagged.
our_version() {
  local version
  version="$(learnhouse_version)"
  echo "${version#*.}.${1:-0}"
}

learnhouse_commit() {
  git -C lib/learnhouse rev-parse HEAD
}

quiet() {
  "$@" > /dev/null
}
