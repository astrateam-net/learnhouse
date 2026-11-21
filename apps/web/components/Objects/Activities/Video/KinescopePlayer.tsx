'use client'

import React, { useEffect, useRef } from 'react'

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

export interface KinescopePlayerProps {
  url: string
  title?: string
  autoplay?: boolean
  muted?: boolean
  startTime?: number
  endTime?: number | null
  onProgress?: (_currentTime: number, _duration: number) => void
  onEnded?: () => void
  onPlayer?: (_player: any) => void
}

export default function KinescopePlayer({
  url,
  title,
  autoplay = false,
  muted = false,
  startTime = 0,
  endTime = null,
  onProgress,
  onEnded,
  onPlayer,
}: KinescopePlayerProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const callbacks = useRef({ onProgress, onEnded, onPlayer })
  useEffect(() => {
    callbacks.current = { onProgress, onEnded, onPlayer }
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
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
      callbacks.current.onEnded?.()
    }

    loadKinescopeFactory()
      .then((factory) =>
        factory.create(mount.id, {
          url,
          size: { width: '100%', height: '100%' },
          behavior: { autoPlay: autoplay, muted },
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
          if (startTime > 0) created.seekTo(startTime)
        })
        created.on(Events.DurationChange, (event: any) => {
          duration = Number(event?.data?.duration) || duration
        })
        created.on(Events.TimeUpdate, (event: any) => {
          const currentTime = Number(event?.data?.currentTime)
          if (!Number.isFinite(currentTime)) return
          if (endTime && currentTime >= endTime) {
            created.pause()
            finish()
            return
          }
          if (duration > 0) callbacks.current.onProgress?.(currentTime, duration)
        })
        created.on(Events.Ended, finish)
        created.on(Events.Play, () => {
          ended = false
        })
        callbacks.current.onPlayer?.(created)
      })
      .catch(() => {})

    return () => {
      cancelled = true
      const done = player?.destroy?.()
      Promise.resolve(done).finally(() => mount.remove())
    }
  }, [url, autoplay, muted, startTime, endTime])

  return <div ref={hostRef} title={title} className="absolute inset-0 h-full w-full bg-[#0d0d0e]" />
}
