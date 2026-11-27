'use client'

/*
 Kinescope through its IFrame Player API
 (player.kinescope.io/latest/iframe.player.js, loaded on first use). Reports
 progress, the end, rate changes and the player controls the same way the
 Vimeo player does, so analytics, resume and notes work unchanged.
*/
import React, { useEffect, useRef } from 'react'
import { kinescopeVideoUrl } from '@/lib/media/kinescope'
import { UnsupportedVideo } from './ExternalVideoPlayer'
import type { ExternalPlayerProps } from './providers/types'

const API_SCRIPT = 'https://player.kinescope.io/latest/iframe.player.js'

let factoryPromise: Promise<any> | null = null

// One IFrame API per page: a second copy of the script throws.
function loadKinescopeFactory(): Promise<any> {
  if (factoryPromise) return factoryPromise
  factoryPromise = new Promise((resolve, reject) => {
    const w = window as any
    if (w.Kinescope?.IframePlayer) {
      resolve(w.Kinescope.IframePlayer)
      return
    }
    const previous = w.onKinescopeIframeAPIReady
    w.onKinescopeIframeAPIReady = (factory: any) => {
      previous?.(factory)
      resolve(factory)
    }
    const script = document.createElement('script')
    script.src = API_SCRIPT
    script.async = true
    script.onerror = () => {
      factoryPromise = null
      script.remove()
      reject(new Error('Kinescope player failed to load'))
    }
    document.head.appendChild(script)
  })
  return factoryPromise
}

let nextId = 0

export default function KinescopePlayer({
  url,
  title,
  startTime,
  endTime,
  autoplay,
  muted,
  resumeAt,
  playbackRate,
  onProgress,
  onEnded,
  onRateChange,
  onReady,
}: ExternalPlayerProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const videoUrl = kinescopeVideoUrl(url)
  const latest = useRef({ startTime, endTime, resumeAt, playbackRate, onProgress, onEnded, onRateChange, onReady })
  useEffect(() => {
    latest.current = { startTime, endTime, resumeAt, playbackRate, onProgress, onEnded, onRateChange, onReady }
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host || !videoUrl) return
    let cancelled = false
    let player: any = null
    let duration = 0
    let ended = false

    // create() swaps the element for its iframe, so it gets a node React does
    // not own.
    const mount = document.createElement('div')
    mount.id = `kinescope-player-${++nextId}`
    mount.className = 'h-full w-full'
    host.appendChild(mount)

    const finish = () => {
      if (ended) return
      ended = true
      latest.current.onEnded?.()
    }

    loadKinescopeFactory()
      .then((factory) =>
        factory.create(mount.id, {
          url: videoUrl,
          size: { width: '100%', height: '100%' },
          behavior: { autoPlay: !!autoplay, muted: !!muted, playsInline: true },
        })
      )
      .then((created: any) => {
        if (cancelled) {
          created.destroy?.()
          return
        }
        player = created
        const Events = created.Events
        created.once(Events.Loaded, (event: any) => {
          duration = Number(event?.data?.duration) || 0
          const { resumeAt: resume, startTime: start, playbackRate: rate } = latest.current
          const from = resume || start || 0
          if (from > 0) created.seekTo(from)
          if (rate && rate !== 1) created.setPlaybackRate?.(rate)
          latest.current.onReady?.({
            getTime: () =>
              Promise.resolve(created.getCurrentTime())
                .then((t: unknown) => (Number.isFinite(Number(t)) ? Number(t) : null))
                .catch(() => null),
            seek: (s) => created.seekTo(Math.max(0, s)),
          })
        })
        created.on(Events.DurationChange, (event: any) => {
          duration = Number(event?.data?.duration) || duration
        })
        created.on(Events.TimeUpdate, (event: any) => {
          const currentTime = Number(event?.data?.currentTime)
          if (!Number.isFinite(currentTime)) return
          const end = latest.current.endTime
          if (end && currentTime >= end) {
            created.pause()
            finish()
            return
          }
          if (duration > 0) latest.current.onProgress?.(currentTime, duration)
        })
        created.on(Events.Ended, finish)
        created.on(Events.Play, () => {
          ended = false
        })
        if (Events.PlaybackRateChange) {
          created.on(Events.PlaybackRateChange, (event: any) => {
            const rate = Number(event?.data?.playbackRate)
            if (Number.isFinite(rate)) latest.current.onRateChange?.(rate)
          })
        }
      })
      .catch(() => {})

    return () => {
      cancelled = true
      const done = player?.destroy?.()
      Promise.resolve(done).finally(() => mount.remove())
    }
  }, [videoUrl, autoplay, muted])

  if (!videoUrl) return <UnsupportedVideo url={url} />
  return <div ref={hostRef} title={title} className="absolute inset-0 h-full w-full bg-[#0d0d0e]" />
}
