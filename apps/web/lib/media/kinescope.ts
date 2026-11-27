/*
 Kinescope (kinescope.io) video links: the share link (kinescope.io/<id>) and
 the embed link (kinescope.io/embed/<id>). Ids are short slugs, numbers or
 UUIDs, the same rule as kinescope_id() in the API's embeds.py.
*/

const KINESCOPE_PATH = /^\/(?:embed\/)?([A-Za-z0-9][A-Za-z0-9-]{4,63})\/?$/

function parse(url: string | null | undefined): URL | null {
  if (!url) return null
  try {
    const u = new URL(url.trim())
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return null
    return u.hostname.toLowerCase().replace(/^www\./, '') === 'kinescope.io' ? u : null
  } catch {
    return null
  }
}

/** The video id of a Kinescope share or embed link, or null. */
export function kinescopeId(url: string | null | undefined): string | null {
  const u = parse(url)
  const m = u ? KINESCOPE_PATH.exec(u.pathname) : null
  return m && m[1].toLowerCase() !== 'embed' ? m[1] : null
}

/** The share link, which is what the IFrame Player API's create() takes. */
export function kinescopeVideoUrl(url: string | null | undefined): string | null {
  const id = kinescopeId(url)
  return id ? `https://kinescope.io/${id}` : null
}

/** The iframe src, keeping a start time (`t`, seconds) like the API does. */
export function kinescopeEmbedUrl(url: string | null | undefined): string | null {
  const id = kinescopeId(url)
  if (!id) return null
  const t = parse(url)?.searchParams.get('t') ?? ''
  return `https://kinescope.io/embed/${id}${/^\d+$/.test(t) ? `?t=${t}` : ''}`
}
