#!/usr/bin/env bash
#MISE description="Upstream's own PR gates on what the series and overlay touch: ruff, strict ESLint, tsc, SDK schema"
#MISE dir="{{config_root}}"
#MISE depends=["up"]
# The same checks a LearnHouse pull request must pass, so every patch stays upstreamable.
# Needs `bun install --frozen-lockfile` in apps/web and `uv sync --frozen` in apps/api.
set -Eeuo pipefail

# Inherited git env would point `git -C` at the outer repo — see .mise/lib.sh.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY

touched() {
  {
    [ -d apps ] && find apps -type f
    grep -hs '^+++ [^/]*/lib/learnhouse/' patches/*.diff | sed 's|^+++ [^/]*/lib/learnhouse/||; s/[[:space:]].*$//'
  } | sort -u
}

main() {
  local files api_py web_js
  files="$(touched)"
  api_py="$(grep -E '^apps/api/.*\.py$' <<< "$files" | sed 's|^apps/api/||' || true)"
  web_js="$(grep -E '^apps/web/.*\.(ts|tsx|js|jsx|mjs)$' <<< "$files" | sed 's|^apps/web/||' || true)"

  if [ -n "$api_py" ]; then
    echo "ruff (api-lint.yaml)"
    # shellcheck disable=SC2086  # one argument per file
    (cd lib/learnhouse/apps/api && ruff check $api_py)
  fi

  if [ -n "$web_js" ]; then
    echo "strict ESLint on touched files (web-lint.yaml)"
    # shellcheck disable=SC2086  # one argument per file
    (cd lib/learnhouse/apps/web && bun run lint:strict --no-error-on-unmatched-pattern $web_js)
    echo "type check (web-lint.yaml typecheck)"
    (cd lib/learnhouse/apps/web && bunx next typegen > /dev/null && bunx tsc --noEmit)
  fi

  if [ -n "$api_py" ]; then
    echo "SDK schema is current (sdk-schema.yaml)"
    (cd lib/learnhouse/packages/sdk && node scripts/generate.mjs --check)
  fi
}

main "$@"
