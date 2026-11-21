/*
 Kinescope (kinescope.io) video links.

 Accepts the share link (kinescope.io/<id>) and the embed link
 (kinescope.io/embed/<id>); ids are numeric or base62. Player parameters
 (t, seek, duration, ...) ride along to the embed URL.
*/

const KINESCOPE_URL = /^https?:\/\/(?:www\.)?kinescope\.io\/(?:embed\/)?([A-Za-z0-9]+)\/?(\?[^#]*)?(?:#.*)?$/i

function match(url: string | null | undefined) {
  return url ? KINESCOPE_URL.exec(url.trim()) : null
}

export function isKinescopeUrl(url: string | null | undefined): boolean {
  return match(url) !== null
}

/** The share link, which is what the IFrame Player API's create() takes. */
export function kinescopeVideoUrl(url: string | null | undefined): string | null {
  const m = match(url)
  return m ? `https://kinescope.io/${m[1]}` : null
}

/** The plain iframe src, for embeds that do not drive the player. */
export function kinescopeEmbedUrl(url: string | null | undefined): string | null {
  const m = match(url)
  return m ? `https://kinescope.io/embed/${m[1]}${m[2] ?? ''}` : null
}
