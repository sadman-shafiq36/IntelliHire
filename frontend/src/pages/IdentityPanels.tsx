// confirming who joined the interview. the candidate's page asks for consent and, if they
// agree, a photo from their camera; only a template of it is kept. the interviewer's room
// shows what the face match found, lets them decide when it can't, and opens the
// candidate's profile once identity is settled. never a silent assumption either way

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Lock, RefreshCw, ShieldCheck, UserCheck, UserX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type { IdentityState, IdentityStatus, LinkIdentity, RoomProfile, SessionDetails } from '@/api/types'
import { Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}

// --- the candidate's page ----------------------------------------------------------------

const FRAMES = 3

async function takePhotos(video: HTMLVideoElement): Promise<string[]> {
  const canvas = document.createElement('canvas')
  const scale = Math.min(1, 640 / (video.videoWidth || 640))
  canvas.width = Math.round((video.videoWidth || 640) * scale)
  canvas.height = Math.round((video.videoHeight || 480) * scale)
  const out: string[] = []
  for (let i = 0; i < FRAMES; i++) {
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
    out.push(canvas.toDataURL('image/jpeg', 0.85))
    await new Promise((r) => setTimeout(r, 350))
  }
  canvas.width = canvas.height = 0
  return out
}

export function FaceConsentCard({ token }: { token: string }) {
  const queryClient = useQueryClient()
  const path = `/api/v1/interviews/join/${encodeURIComponent(token)}/identity`
  const key = ['link-identity', token]
  const status = useQuery({
    queryKey: key,
    queryFn: () => api.get<LinkIdentity>(path, { authenticated: false }),
    retry: false,
  })
  const [capturing, setCapturing] = useState(false)
  const [cameraError, setCameraError] = useState<string | null>(null)
  const video = useRef<HTMLVideoElement>(null)
  const stream = useRef<MediaStream | null>(null)

  function stopCamera() {
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
    setCapturing(false)
  }
  useEffect(() => stopCamera, [])

  const send = useMutation({
    mutationFn: (images: string[]) => api.post<LinkIdentity>(`${path}/photo`, { images }, { authenticated: false }),
    onSuccess: (d) => {
      queryClient.setQueryData(key, d)
      stopCamera()
    },
  })
  const refuse = useMutation({
    mutationFn: () => api.post<LinkIdentity>(`${path}/decline`, undefined, { authenticated: false }),
    onSuccess: (d) => {
      queryClient.setQueryData(key, d)
      stopCamera()
    },
  })

  async function openCamera() {
    setCameraError(null)
    send.reset()
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false })
      setCapturing(true)
      requestAnimationFrame(() => {
        if (video.current && stream.current) {
          video.current.srcObject = stream.current
          void video.current.play()
        }
      })
    } catch {
      setCameraError("Your camera couldn't be opened. Allow camera access in the browser and try again.")
    }
  }

  const d = status.data
  if (status.isLoading || status.error || !d) return null
  const on = d.consent === 'given'
  const waiting = on && d.reference === 'pending'
  const unusable = on && d.reference === 'unusable'

  return (
    <section aria-labelledby="face-check-heading" className="mb-6 rounded-2xl border border-white/10 bg-white/5 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <ShieldCheck aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-brand-200" />
        <div className="min-w-0 flex-1">
          <h2 id="face-check-heading" className="text-base font-semibold text-white">
            Confirm it's you <span className="font-normal text-slate-400">(optional)</span>
          </h2>
          {on && d.reference === 'ready' && !capturing && (
            <p className="mt-1 text-sm text-slate-300">
              Face check is on, using the photo {d.source === 'form' ? 'you sent with your application' : 'you took here'}.
              Only an encrypted template is kept, and it is deleted after your interview.
            </p>
          )}
          {waiting && !capturing && (
            <p className="mt-1 text-sm text-slate-300">
              Face check is on, using the photo you sent with your application. You can take a new one here instead.
            </p>
          )}
          {unusable && !capturing && (
            <p className="mt-1 text-sm text-amber-200">
              The photo you sent couldn't be used. Take one here, or the interviewer will confirm who you are on the call.
            </p>
          )}
          {!on &&
            !capturing &&
            (d.consent ? (
              <p className="mt-1 text-sm text-slate-300">Face check is off. The interviewer will confirm who you are on the call.</p>
            ) : (
              <p className="mt-1 text-sm leading-relaxed text-slate-300">{d.text}</p>
            ))}

          {capturing && (
            <div className="mt-3">
              <p className="text-sm text-slate-300">{d.text}</p>
              <video
                ref={video}
                muted
                playsInline
                aria-label="Your camera"
                className="mt-3 aspect-video w-full max-w-sm rounded-lg bg-black object-cover [transform:scaleX(-1)]"
              />
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {capturing ? (
              <>
                <button
                  type="button"
                  disabled={send.isPending}
                  onClick={async () => video.current && send.mutate(await takePhotos(video.current))}
                  className="inline-flex items-center gap-1.5 rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                >
                  <Camera aria-hidden="true" className="h-4 w-4" />
                  {send.isPending ? 'Checking the photo' : 'I agree, take the photo'}
                </button>
                <button type="button" onClick={stopCamera} className="rounded-md px-3 py-2 text-sm text-slate-300 hover:text-white">
                  Cancel
                </button>
              </>
            ) : (
              <>
                {(!on || waiting || unusable) && (
                  <button
                    type="button"
                    onClick={() => void openCamera()}
                    className="inline-flex items-center gap-1.5 rounded-md bg-white/10 px-3 py-2 text-sm font-medium text-white hover:bg-white/20"
                  >
                    <Camera aria-hidden="true" className="h-4 w-4" />
                    {on ? 'Take a photo here' : d.consent ? 'Turn face check on' : 'Agree and take a photo'}
                  </button>
                )}
                {d.consent !== 'declined' && d.consent !== 'withdrawn' && (
                  <button
                    type="button"
                    disabled={refuse.isPending}
                    onClick={() => refuse.mutate()}
                    className="rounded-md px-3 py-2 text-sm text-slate-300 hover:text-white disabled:opacity-50"
                  >
                    {on ? 'Withdraw and delete my template' : 'No thanks'}
                  </button>
                )}
              </>
            )}
          </div>
          {(cameraError || send.error || refuse.error) && (
            <p role="alert" className="mt-2 text-sm text-red-200">
              {cameraError ?? message(send.error ?? refuse.error, 'That did not work. Try again.')}
            </p>
          )}
        </div>
      </div>
    </section>
  )
}

// --- the interviewer's room ---------------------------------------------------------------

export function useIdentity(interviewId: number, live: boolean) {
  return useQuery({
    queryKey: ['identity', interviewId],
    queryFn: () => api.get<IdentityState>(`/api/v1/interviews/${interviewId}/identity`),
    // the transcriber pushes a new result over the call; this catches anything missed
    refetchInterval: live ? 8000 : false,
  })
}

const TONE: Record<IdentityStatus, 'ok' | 'warn' | 'bad' | 'neutral'> = {
  pending: 'neutral',
  confirmed: 'ok',
  manually_confirmed: 'ok',
  low_confidence: 'warn',
  not_identified: 'warn',
  mismatch: 'bad',
  rejected: 'bad',
}
const TITLE: Record<IdentityStatus, string> = {
  pending: 'Identity not checked yet',
  confirmed: 'Identity confirmed by face match',
  manually_confirmed: 'Identity confirmed by you',
  low_confidence: 'Identity uncertain',
  not_identified: 'Not identified',
  mismatch: 'May not be the candidate',
  rejected: 'Not the candidate',
}

export function IdentityBanner({ session }: { session: SessionDetails }) {
  const queryClient = useQueryClient()
  const live = session.status === 'in_progress'
  const identity = useIdentity(session.id, live)
  const key = ['identity', session.id]
  const decide = useMutation({
    mutationFn: (decision: 'confirm' | 'reject') =>
      api.post<IdentityState>(`/api/v1/interviews/${session.id}/identity/decision`, { decision }),
    onSuccess: (d) => {
      queryClient.setQueryData(key, d)
      void queryClient.invalidateQueries({ queryKey: ['profile', session.id] })
    },
  })
  const retry = useMutation({
    mutationFn: () => api.post(`/api/v1/interviews/${session.id}/identity/retry`),
  })

  const d = identity.data
  if (!d) return null
  const tone = TONE[d.status]
  const settled = d.status === 'confirmed' || d.status === 'manually_confirmed'
  return (
    <div
      role="status"
      className={cn(
        'mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border px-3 py-2 text-sm',
        tone === 'ok' && 'border-brand-500/40 bg-brand-500/10 text-brand-100',
        tone === 'warn' && 'border-amber-400/40 bg-amber-400/10 text-amber-100',
        tone === 'bad' && 'border-red-400/50 bg-red-500/10 text-red-100',
        tone === 'neutral' && 'border-white/10 bg-white/5 text-slate-300',
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="font-medium">{TITLE[d.status]}</p>
        <p className="text-xs opacity-90">
          {d.message}
          {!settled && d.status !== 'rejected' && ' Check who you are speaking to before you rely on the profile.'}
        </p>
      </div>
      {retry.isSuccess && <span className="text-xs opacity-80">Looking again…</span>}
      <div className="flex flex-wrap gap-2">
        {live && d.consent === 'given' && d.status !== 'manually_confirmed' && (
          <button
            type="button"
            onClick={() => retry.mutate()}
            disabled={retry.isPending}
            className="inline-flex items-center gap-1 rounded-md bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20 disabled:opacity-50"
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Check again
          </button>
        )}
        {!settled && (
          <button
            type="button"
            onClick={() => decide.mutate('confirm')}
            disabled={decide.isPending}
            className="inline-flex items-center gap-1 rounded-md bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20 disabled:opacity-50"
          >
            <UserCheck aria-hidden="true" className="h-3.5 w-3.5" />
            I've confirmed it's them
          </button>
        )}
        {d.status !== 'rejected' && (
          <button
            type="button"
            onClick={() => decide.mutate('reject')}
            disabled={decide.isPending}
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-white/80 hover:bg-white/10 hover:text-white disabled:opacity-50"
          >
            <UserX aria-hidden="true" className="h-3.5 w-3.5" />
            Not the candidate
          </button>
        )}
      </div>
      {(decide.error || retry.error) && (
        <p role="alert" className="w-full text-xs text-red-200">
          {message(decide.error ?? retry.error, 'That did not work. Try again.')}
        </p>
      )}
    </div>
  )
}

const SCREENING: Record<NonNullable<RoomProfile['probe'][number]['screening']>, string> = {
  met: 'shown in the CV',
  partially_met: 'partly shown in the CV',
  unmet: 'not found in the CV',
}

export function ProfilePanel({ session }: { session: SessionDetails }) {
  const identity = useIdentity(session.id, session.status === 'in_progress')
  const open = identity.data?.profile_available === true
  const profile = useQuery({
    queryKey: ['profile', session.id],
    queryFn: () => api.get<RoomProfile>(`/api/v1/interviews/${session.id}/profile`),
    enabled: open,
  })

  if (!open)
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-slate-300">
        <Lock aria-hidden="true" className="h-5 w-5 text-slate-400" />
        <p>The profile opens once you know who you're speaking to: a confident face match, or your own confirmation.</p>
      </div>
    )
  if (profile.isLoading)
    return (
      <div className="p-4">
        <Spinner />
      </div>
    )
  if (profile.error || !profile.data)
    return <p className="p-4 text-sm text-red-200">{message(profile.error, 'The profile could not be loaded.')}</p>

  const p = profile.data
  return (
    <div className="h-full space-y-4 overflow-y-auto px-4 py-3 text-sm text-slate-200">
      <div>
        <p className="text-base font-semibold text-white">{p.name}</p>
        {p.headline && <p className="text-slate-300">{p.headline}</p>}
        <p className="mt-1 text-xs text-slate-400">
          {p.years_experience ? `${p.years_experience} years' experience` : 'Experience not stated'}
          {p.current_role && ` · ${p.current_role.title}${p.current_role.organisation ? `, ${p.current_role.organisation}` : ''}`}
        </p>
      </div>
      <div className="flex gap-3">
        <div className="rounded-lg bg-white/5 px-3 py-2">
          <p className="text-xs text-slate-400">Pre-interview score</p>
          <p className="tabular text-xl font-semibold text-white">{p.score ?? '–'}</p>
        </div>
        {p.rank !== null && (
          <div className="rounded-lg bg-white/5 px-3 py-2">
            <p className="text-xs text-slate-400">Rank</p>
            <p className="tabular text-xl font-semibold text-white">{p.rank}</p>
          </div>
        )}
      </div>
      {p.skills.length > 0 && (
        <section aria-label="Key skills">
          <p className="text-xs font-medium text-slate-400">Key skills</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {p.skills.map((s) => (
              <li key={s} className="rounded-full bg-white/10 px-2 py-0.5 text-xs">
                {s}
              </li>
            ))}
          </ul>
        </section>
      )}
      {p.probe.length > 0 && (
        <section aria-label="Areas to probe">
          <p className="text-xs font-medium text-slate-400">Areas to probe</p>
          <ul className="mt-1 space-y-1.5">
            {p.probe.map((x) => (
              <li key={x.text} className="rounded-lg bg-white/5 px-3 py-2">
                <p className="text-white">
                  {x.text}
                  {x.must_have && <span className="ml-1 text-xs text-amber-200">must-have</span>}
                </p>
                {x.screening && <p className="text-xs text-slate-400">{SCREENING[x.screening]}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {p.requirements_met.length > 0 && (
        <section aria-label="Requirements shown in the CV">
          <p className="text-xs font-medium text-slate-400">Shown in the CV</p>
          <ul className="mt-1 space-y-1.5">
            {p.requirements_met.map((r) => (
              <li key={r.text}>
                <p>{r.text}</p>
                {r.quote && (
                  <p className="mt-0.5 border-l-2 border-marker pl-2 text-xs text-slate-300">
                    <mark className="rounded-sm bg-marker px-0.5 text-slate-900">{r.quote}</mark>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {p.summary && <p className="text-xs text-slate-400">Screening: {p.summary}</p>}
    </div>
  )
}
