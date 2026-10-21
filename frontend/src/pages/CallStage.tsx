// the call itself: the other person large, yourself small in the corner, and the
// controls. used by both the interviewer's room and the candidate's page

import type { Track } from 'livekit-client'
import { Mic, MicOff, PhoneOff, UserRound, Video, VideoOff, Volume2, WifiOff } from 'lucide-react'
import { type ReactNode, useEffect, useRef } from 'react'

import type { Call } from '@/hooks/useCall'
import { cn } from '@/lib/cn'

export function VideoView({ track, mirror }: { track: Track; mirror?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    track.attach(el)
    return () => {
      track.detach(el)
    }
  }, [track])
  return (
    <video
      ref={ref}
      autoPlay
      playsInline
      muted
      className={cn('h-full w-full object-cover', mirror && '-scale-x-100')}
    />
  )
}

export function RoundButton({
  label,
  on = true,
  danger,
  disabled,
  onClick,
  children,
}: {
  label: string
  on?: boolean
  danger?: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={danger ? undefined : !on}
      title={label}
      className={cn(
        'flex h-12 w-12 items-center justify-center rounded-full text-white transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-200',
        'disabled:pointer-events-none disabled:opacity-45',
        danger
          ? 'bg-red-700 hover:bg-red-800'
          : on
            ? 'bg-white/15 hover:bg-white/25'
            : 'bg-red-600/90 hover:bg-red-700',
      )}
    >
      {children}
    </button>
  )
}

export function CallStage({
  call,
  otherLabel,
  waitingFor,
  extraControls,
  onLeave,
}: {
  call: Call
  otherLabel: string
  // shown in the big tile until the other person is in the room
  waitingFor: string
  extraControls?: ReactNode
  onLeave: () => void
}) {
  const other = call.people[0]
  return (
    <div className="flex flex-col gap-4">
      {call.state === 'reconnecting' && (
        <p role="status" className="flex items-center gap-2 rounded-lg bg-amber-500/15 px-3 py-2 text-sm text-amber-100">
          <WifiOff aria-hidden="true" className="h-4 w-4" />
          Connection lost. Reconnecting… The interview and its transcript are kept.
        </p>
      )}
      {call.audioBlocked && (
        <button
          type="button"
          onClick={() => void call.startAudio()}
          className="flex items-center gap-2 self-start rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white"
        >
          <Volume2 aria-hidden="true" className="h-4 w-4" />
          Your browser blocked the sound. Click to hear the other person.
        </button>
      )}

      <div className="relative aspect-video overflow-hidden rounded-2xl bg-slate-800">
        {other?.video ? (
          <VideoView track={other.video} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-slate-400">
            <UserRound aria-hidden="true" className="h-10 w-10" />
            <p className="text-sm">{other ? 'Camera is off' : waitingFor}</p>
          </div>
        )}
        {other && (
          <span
            className={cn(
              'absolute bottom-3 left-3 flex items-center gap-1.5 rounded-md bg-black/50 px-2 py-1 text-sm text-white',
              other.speaking && 'ring-2 ring-brand-500',
            )}
          >
            {other.micMuted && <MicOff aria-label="muted" className="h-3.5 w-3.5 text-red-300" />}
            {otherLabel}
          </span>
        )}

        <div
          className={cn(
            'absolute right-3 top-3 aspect-video w-28 overflow-hidden rounded-lg border border-white/20 bg-slate-700 shadow-lg sm:w-44',
            call.selfSpeaking && call.micOn && 'ring-2 ring-brand-500',
          )}
        >
          {call.selfVideo ? (
            <VideoView track={call.selfVideo} mirror />
          ) : (
            <div className="flex h-full items-center justify-center text-slate-400">
              <VideoOff aria-hidden="true" className="h-5 w-5" />
            </div>
          )}
          <span className="absolute bottom-1 left-1 rounded bg-black/50 px-1.5 text-xs text-white">You</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3" role="group" aria-label="Call controls">
        <RoundButton
          label={call.micOn ? 'Mute microphone' : 'Unmute microphone'}
          on={call.micOn}
          onClick={() => void call.toggleMic()}
        >
          {call.micOn ? <Mic aria-hidden="true" className="h-5 w-5" /> : <MicOff aria-hidden="true" className="h-5 w-5" />}
        </RoundButton>
        <RoundButton
          label={call.camOn ? 'Turn camera off' : 'Turn camera on'}
          on={call.camOn}
          onClick={() => void call.toggleCam()}
        >
          {call.camOn ? <Video aria-hidden="true" className="h-5 w-5" /> : <VideoOff aria-hidden="true" className="h-5 w-5" />}
        </RoundButton>
        <RoundButton label="Leave the call" danger onClick={onLeave}>
          <PhoneOff aria-hidden="true" className="h-5 w-5" />
        </RoundButton>
        {extraControls}
      </div>
    </div>
  )
}
