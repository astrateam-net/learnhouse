#!/usr/bin/env bash
#MISE description="Tests the series and the overlay carry: their bun test files and pytest modules"
#MISE dir="{{config_root}}"
#MISE depends=["up"]
# Scoped to what the series and overlay touch: upstream's whole suite needs Postgres and Redis.
set -Eeuo pipefail

# Inherited git env would point `git -C` at the outer repo — see .mise/lib.sh.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY

# Every path the overlay or a patch owns, relative to lib/learnhouse.
touched() {
  {
    [ -d apps ] && find apps -type f
    grep -hs '^+++ [^/]*/lib/learnhouse/' patches/*.diff | sed 's|^+++ [^/]*/lib/learnhouse/||; s/[[:space:]].*$//'
  } | sort -u
}

main() {
  local files web_tests api_tests
  files="$(touched)"
  web_tests="$(grep -E '^apps/web/tests/.*\.test\.m?[jt]sx?$' <<< "$files" | sed 's|^apps/web/||' || true)"
  api_tests="$(grep -E '^apps/api/src/tests/.*/test_[A-Za-z0-9_]+\.py$' <<< "$files" | sed 's|^apps/api/||' || true)"
  if [ -z "$web_tests" ] && [ -z "$api_tests" ]; then
    echo "error: the series and overlay carry no tests" >&2
    return 1
  fi

  if [ -n "$web_tests" ]; then
    pushd lib/learnhouse/apps/web > /dev/null
    # shellcheck disable=SC2086  # one argument per test file
    bun test --isolate $web_tests
    popd > /dev/null
  fi
  if [ -n "$api_tests" ]; then
    pushd lib/learnhouse/apps/api > /dev/null
    # SQLAlchemy only pulls greenlet in on x86_64/aarch64; macOS arm64 needs it named.
    # shellcheck disable=SC2086  # one argument per test module
    uv run --frozen --with greenlet pytest -q $api_tests
    popd > /dev/null
  fi
}

main "$@"
