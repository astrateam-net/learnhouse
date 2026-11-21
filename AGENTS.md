# learnhouse

Upstream [LearnHouse](https://github.com/learnhouse/learnhouse) (Next.js web, FastAPI API, AGPL-3.0)
pinned as the `lib/learnhouse` submodule, plus a quilt series and our own new files. The artifact is
the community image of the patched tree, built with upstream's own Dockerfile. First capability:
Kinescope video — as a video activity with progress tracking, as an embed, and from the AI/MCP tools.

## Every patch is an upstream pull request

This repo exists so changes can go back to LearnHouse. **A patch is written, tested and described
as the pull request it will become**, and the series is ordered so each one applies alone on top of
the ones before it. To open the PR: fork learnhouse/learnhouse, apply the patch plus the overlay
files its header names, push, open the PR with the header as its description.

## Two owners, disjoint

**`patches/` modifies files that exist upstream. The overlay adds files that do not.** No path may be
owned by both; `mise run overlay --check` fails if one is.

The overlay root `apps/` mirrors the upstream directory of the same name:
`apps/web/lib/media/kinescope.ts` is copied to `lib/learnhouse/apps/web/lib/media/kinescope.ts`.

The order is always `quilt push -a` → `mise run overlay` → build or test; `mise run up` is both halves.

## Commands

| Task | Command |
| --- | --- |
| Assemble the tree | `mise run up` |
| Back to pristine upstream | `mise run down` |
| Who owns a file | `mise run owner <path>` |
| Series integrity | `mise run test:series` |
| Tests the series and overlay carry (bun test + pytest) | `mise run test:unit` |
| Upstream's PR gates on what we touch (ruff, strict ESLint, tsc, SDK schema) | `mise run lint:upstream` |
| Lint our own files | `mise run lint` |
| Everything that gates a push | `mise run check` |
| Build the image and run `tests.yaml` | `mise run build [patch]` — tags `learnhouse:4.5.<patch>` |
| Publish `ghcr.io/astrateam-net/learnhouse:4.5.N` | dispatch `Draft release` with `version` (upstream's, e.g. `1.4.5`) and `patch` = N, then publish the draft; a published tag is never overwritten |

`test:unit` and `lint:upstream` need upstream's dependencies, once per checkout:
`(cd lib/learnhouse/apps/web && bun install --frozen-lockfile)` and
`(cd lib/learnhouse/apps/api && uv sync --frozen)`.

quilt is a system package (`brew install quilt`), not a mise tool. Never run `quilt` outside `mise`:
`.quiltrc` reaches quilt only through the `QUILTRC` that mise exports.

## Write facts, not prose

**A comment states what and why in one or two lines. Never more than the code it explains.** This
binds every doc and every comment in the repo.

## Write it the way LearnHouse does

Code in patches and overlay reads as if a LearnHouse maintainer wrote it, because it is meant to be
merged there:

- Follow the file you are in. Overlay files follow upstream's style, not ours: they are excluded
  from our linters and checked by upstream's (`mise run lint:upstream`).
- New UI strings go into **every** locale in `apps/web/locales/`, translated — upstream's own
  feature PRs do. Brand names (YouTube, Kinescope) stay untranslated.
- The embed providers live twice, `apps/web/.../embeds/embedProviders.ts` and
  `apps/api/src/services/ai/tools/embeds.py`; a provider change lands in both and in both test
  tables (`editor-blocks.test.mjs`, `test_block_specs.py`).
- A change to an API model or route regenerates the SDK schema with upstream's script
  (`packages/sdk/scripts/generate.mjs`); never hand-edit generated files.
- Tests extend upstream's own tables and suites where one exists, and run on SQLite: no network.
- Commits upstream are conventional with a scope: `feat(api): …`, `fix(web): …`.

## Invariants

- `patches/**` or `lib/learnhouse/**` → read `.claude/rules/quilt-mechanics.md` before editing.
- **A patch applying cleanly is not acceptance.** It must still be needed and still be tested: a
  test that fails without it and passes with it.
- **An unverified claim is labelled unverified.**
- Commits here are conventional (`feat: …`, lowercase imperative, about 60 characters), enforced by hk.
