## What this changes

<!-- One or two lines. A patch's rationale header carries the detail; do not repeat it here. -->

## Owner

<!-- Tick one. `mise run owner <path>` names it for you. -->

- [ ] `patches/` — modifies a file that exists upstream. Managed with quilt, never edited by hand.
- [ ] Overlay (`apps/`) — a file upstream does not have.
- [ ] Neither — docs, CI or tooling.

## Checks

- [ ] `mise run check` passes.
- [ ] A test fails without this change and passes with it. If it is a patch, the header names that test.
- [ ] Nothing in the patch could have lived in the overlay instead.
- [ ] The patch still reads as an upstream pull request: upstream style, every locale, `mise run lint:upstream` passes.
