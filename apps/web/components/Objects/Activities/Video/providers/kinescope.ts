import React from 'react'
import { kinescopeEmbedUrl, kinescopeId } from '@/lib/media/kinescope'
import type { VideoProvider } from './types'

/** Kinescope, played through its IFrame Player API with progress and resume. */
export const kinescopeVideoProvider: VideoProvider = {
  id: 'kinescope',
  label: 'Kinescope',
  match: (url) => {
    const videoId = kinescopeId(url)
    const embedUrl = kinescopeEmbedUrl(url)
    if (!videoId || !embedUrl) return null
    return { provider: 'kinescope', label: 'Kinescope', videoId, embedUrl, sourceUrl: url }
  },
  Player: React.lazy(() => import('../KinescopePlayer')),
}
