# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions mirror the LearnHouse version they carry. LearnHouse's major says
nothing yet, so we drop it and keep the two components that move: LearnHouse
`1.4.5` ships as `4.5.0`. The last slot is ours, so `4.5.1` is the same
LearnHouse version with a changed patch series or a newer upstream commit. To
read the LearnHouse version off ours, take the first two fields and put `1.` in
front.

LearnHouse dev builds share a version, so each entry also names the pinned
upstream commit on its own line under the heading.

## Unreleased

LearnHouse 1.4.5 at learnhouse/learnhouse@57abad4 (dev)

### Added

- Single sign-on with OpenID Connect (and WorkOS, untested) in the community
  build: the API side LearnHouse moved into its Enterprise Edition, restored
  from its last public version and moved onto today's sign-in path (two-factor,
  session provenance, custom domains). The web app's SSO settings, button and
  callback page are no longer hidden.
- An existing account signs in through SSO when SSO created it, or when it
  belongs to this organization alone, its email is in an allowed domain and
  the identity provider marks it verified. Backoffice accounts never do.

## 4.5.1 - 2026-11-28

LearnHouse 1.4.5 at learnhouse/learnhouse@57abad4 (dev)

### Changed

- Rebased on upstream dev, which adds a video provider extension point
  (learnhouse/learnhouse#1352): Vimeo and Loom links, resume, trim and
  transcripts for every external video.
- The Kinescope series is cut on that extension point. The API side is
  upstream's own Kinescope support from #1352, which upstream dropped again in
  ec9ebed5a; the web side registers a Kinescope provider in the open-source
  provider list instead of patching the player and dialogs.
- Kinescope ids follow upstream's rule, so UUID ids (with dashes) are accepted.

### Added

- Kinescope playback gets resume, playback rate and the seek/getTime controls
  through the provider's player.
- Kinescope links in LMS course import (HTML iframes and video tags).
- The migration docs list every external video type, Loom included.

## 4.5.0 - 2026-11-22

LearnHouse 1.4.5 at learnhouse/learnhouse@dde7c8e (dev)

### Added

- Kinescope as an external video type in the API (`type: "kinescope"`), with
  the docs and SDK schema.
- Kinescope embeds in the editor's Embed block, the Embed activity and the AI
  tools' `embed` fence, in both provider tables (web and API).
- Kinescope as a video activity source, played through Kinescope's IFrame
  Player API with progress, completion, the notes clock and the activity's
  start/end, autoplay and mute settings; strings in every locale.
- `kind: "kinescope"` for `create_media_activity` and `build_course`, so agents
  over MCP create Kinescope video activities.
