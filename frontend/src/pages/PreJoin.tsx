// camera and microphone check before joining: preview, a level meter that moves when
// you speak, device choice, and whether to start muted. the tracks made here are the
// ones published, so the browser asks for permission once

import {
  createAudioAnalyser,
  createLocalTracks,
  type LocalAudioTrack,
  type LocalTrack,
  type LocalVideoTrack,
  Room,
  Track,
  VideoPresets,
} from 'livekit-client'
import { Mic, MicOff, Video, VideoOff } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'

import { Spinner } from '@/components/ui'
import type { Media } from '@/hooks/useCall'
import { cn } from '@/lib/cn'
import { RoundButton, VideoView } from '@/pages/CallStage'

function explain(error: unknown, what: string): string {
  const name = error instanceof DOMException || error instanceof Error ? error.name : ''
  if (name === 'NotAllowedError')
    return `The browser was not allowed to use your ${what}. Allow it from the icon in the address bar, then reload this page.`
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return `No ${what} was found. Plug one in and reload this page.`
  if (name === 'NotReadableError')
    return `Your ${what} is in use by another app. Close it there and reload this page.`
  return `Your ${what} could not be started. Reload this page to try again.`
}

export function PreJoin({
  canJoin,
  joinLabel,
  busy,
  error,
  waiting,
  onJoin,
}: {
  canJoin: boolean
  joinLabel: string
  busy: boolean
  error?: string | null
  // shown under the button while joining isn't possible yet
  waiting?: ReactNode
  onJoin: (media: Media) => void
}) {
  const [audio, setAudio] = useState<LocalAudioTrack | null>(null)
  const [video, setVideo] = useState<LocalVideoTrack | null>(null)
  const [micOn, setMicOn] = useState(true)
  const [camOn, setCamOn] = useState(true)
  const [loading, setLoading] = useState(true)
  const [problems, setProblems] = useState<string[]>([])
  const [mics, setMics] = useState<MediaDeviceInfo[]>([])
  const [cams, setCams] = useState<MediaDeviceInfo[]>([])
  const [level, setLevel] = useState(0)
  // once handed to the call the tracks belong to it and must not be stopped here
  const handedOver = useRef(false)
  const tracks = useRef<LocalTrack[]>([])

  useEffect(() => {
    let cancelled = false
    async function start() {
      const found: LocalTrack[] = []
      const issues: string[] = []
      try {
        found.push(...(await createLocalTracks({ audio: true, video: { resolution: VideoPresets.h540.resolution } })))
      } catch {
        // try them one at a time to say which one is the problem
        for (const kind of ['audio', 'video'] as const) {
          try {
            found.push(...(await createLocalTracks(kind === 'audio' ? { audio: true } : { video: true })))
          } catch (e) {
            issues.push(explain(e, kind === 'audio' ? 'microphone' : 'camera'))
          }
        }
      }
      if (cancelled) {
        found.forEach((t) => t.stop())
        return
      }
      tracks.current = found
      setAudio((found.find((t) => t.kind === Track.Kind.Audio) as LocalAudioTrack) ?? null)
      setVideo((found.find((t) => t.kind === Track.Kind.Video) as LocalVideoTrack) ?? null)
      setProblems(issues)
      setLoading(false)
      setMics(await Room.getLocalDevices('audioinput', false).catch(() => []))
      setCams(await Room.getLocalDevices('videoinput', false).catch(() => []))
    }
    void start()
    return () => {
      cancelled = true
      if (!handedOver.current) tracks.current.forEach((t) => t.stop())
    }
  }, [])

  // the meter reads the real microphone, so a dead or wrong one is obvious
  useEffect(() => {
    if (!audio) return
    const { calculateVolume, cleanup } = createAudioAnalyser(audio, { cloneTrack: true })
    const id = window.setInterval(() => setLevel(calculateVolume()), 100)
    return () => {
      window.clearInterval(id)
      void cleanup()
    }
  }, [audio])

  function join() {
    handedOver.current = true
    onJoin({ audio, video, micOn, camOn })
  }

  const noMic = !audio && !loading

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] lg:items-center">
      <div className="relative aspect-video overflow-hidden rounded-2xl bg-slate-800">
        {video && camOn ? (
          <VideoView track={video} mirror />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-400">
            {loading ? <Spinner label="Starting your camera" /> : <VideoOff aria-hidden="true" className="h-8 w-8" />}
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-3 p-3">
          <RoundButton
            label={micOn ? 'Turn microphone off' : 'Turn microphone on'}
            on={micOn}
            disabled={!audio}
            onClick={() => setMicOn((v) => !v)}
          >
            {micOn && audio ? <Mic className="h-5 w-5" aria-hidden="true" /> : <MicOff className="h-5 w-5" aria-hidden="true" />}
          </RoundButton>
          <RoundButton
            label={camOn ? 'Turn camera off' : 'Turn camera on'}
            on={camOn}
            disabled={!video}
            onClick={() => setCamOn((v) => !v)}
          >
            {camOn && video ? <Video className="h-5 w-5" aria-hidden="true" /> : <VideoOff className="h-5 w-5" aria-hidden="true" />}
          </RoundButton>
        </div>
      </div>

      <div className="space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-white">Check your camera and microphone</h2>
          <p className="mt-1 text-sm text-slate-300">Say something: the bar should move.</p>
        </div>

        <div>
          <div className="flex items-center gap-3">
            <Mic aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400" />
            <div
              className="h-2 flex-1 overflow-hidden rounded-full bg-white/10"
              role="meter"
              aria-label="Microphone level"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(level * 100)}
            >
              <div
                className={cn('h-full rounded-full transition-[width] duration-100', micOn ? 'bg-brand-500' : 'bg-slate-500')}
                style={{ width: `${Math.min(100, level * 250)}%` }}
              />
            </div>
          </div>
          {!micOn && audio && <p className="mt-1 text-xs text-slate-400">You'll join muted.</p>}
        </div>

        {mics.length > 1 && audio && (
          <DeviceSelect
            label="Microphone"
            devices={mics}
            onChange={(deviceId) => void audio.restartTrack({ deviceId })}
          />
        )}
        {cams.length > 1 && video && (
          <DeviceSelect
            label="Camera"
            devices={cams}
            onChange={(deviceId) => void video.restartTrack({ deviceId })}
          />
        )}

        {problems.map((p) => (
          <p key={p} role="alert" className="rounded-lg bg-amber-500/15 px-3 py-2 text-sm text-amber-100">
            {p}
          </p>
        ))}
        {noMic && (
          <p className="text-sm text-slate-300">
            Without a microphone the other person can't hear you and nothing you say is
            transcribed.
          </p>
        )}

        <button
          type="button"
          onClick={join}
          disabled={!canJoin || busy || loading}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-brand-500 px-5 text-base font-medium text-white transition-colors hover:bg-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-200 disabled:pointer-events-none disabled:opacity-45"
        >
          {busy ? 'Joining…' : joinLabel}
        </button>
        {error && (
          <p role="alert" className="text-sm text-red-200">
            {error}
          </p>
        )}
        {!canJoin && waiting && <div className="text-sm text-slate-300" aria-live="polite">{waiting}</div>}
      </div>
    </div>
  )
}

function DeviceSelect({
  label,
  devices,
  onChange,
}: {
  label: string
  devices: MediaDeviceInfo[]
  onChange: (deviceId: string) => void
}) {
  return (
    <label className="block text-sm text-slate-300">
      {label}
      <select
        className="mt-1 block w-full rounded-md border border-white/15 bg-slate-800 px-3 py-2 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        onChange={(e) => onChange(e.target.value)}
      >
        {devices.map((d, i) => (
          <option key={d.deviceId || i} value={d.deviceId}>
            {d.label || `${label} ${i + 1}`}
          </option>
        ))}
      </select>
    </label>
  )
}
