// the candidate's meeting page, opened from the secure link in their invitation. the
// video call is held here in intellihire, never google meet. they can check their
// camera and microphone at any time and join once the interviewer has opened the room

import { useMutation, useQuery } from '@tanstack/react-query'
import { CalendarClock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { CallConnection, JoinDetails } from '@/api/types'
import { Spinner } from '@/components/ui'
import { type Media, useCall } from '@/hooks/useCall'
import { CallStage } from '@/pages/CallStage'
import { formatWhen } from '@/pages/InterviewsPanel'
import { PreJoin } from '@/pages/PreJoin'

type Phase = 'check' | 'call' | 'left'

function viewerZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function InterviewJoinPage() {
  const { token = '' } = useParams()
  const [phase, setPhase] = useState<Phase>('check')
  const call = useCall()
  const path = `/api/v1/interviews/join/${encodeURIComponent(token)}`
  const details = useQuery({
    queryKey: ['join', token],
    queryFn: () => api.get<JoinDetails>(path, { authenticated: false }),
    retry: false,
    // watching for the interviewer to open the room
    refetchInterval: (q) => (q.state.data?.status === 'scheduled' ? 5000 : false),
  })

  const join = useMutation({
    mutationFn: async (media: Media) => {
      const conn = await api.post<CallConnection>(`${path}/session`, undefined, { authenticated: false })
      await call.join(conn, media)
    },
    onSuccess: () => setPhase('call'),
  })

  useEffect(() => {
    if (phase === 'call' && call.state === 'ended') {
      setPhase('left')
      void details.refetch()
    }
    // refetch is stable for this query
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, call.state])

  const d = details.data
  const zone = viewerZone()

  if (details.isLoading)
    return (
      <Shell>
        <div className="flex flex-1 items-center justify-center text-slate-300">
          <Spinner label="Opening your interview room" />
        </div>
      </Shell>
    )

  if (details.error || !d)
    return (
      <Shell>
        <Notice title="This link can't be opened">
          {details.error instanceof ApiError ? details.error.message : 'Please use the link in your most recent email.'}
        </Notice>
      </Shell>
    )

  if (d.status === 'cancelled')
    return (
      <Shell jobTitle={d.job_title}>
        <Notice title="This interview was cancelled">
          Your interview for {d.job_title} will not take place. Check your email for details.
        </Notice>
      </Shell>
    )

  if (d.status === 'completed' || (phase === 'left' && call.roomClosed))
    return (
      <Shell jobTitle={d.job_title}>
        <Notice title="The interview has ended">
          Thank you for your time. The hiring team will be in touch by email.
        </Notice>
      </Shell>
    )

  const open = d.status === 'in_progress'
  const interviewer = d.interviewer_name ?? 'the interviewer'

  return (
    <Shell jobTitle={d.job_title}>
      <div className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:py-10">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm text-slate-400">Interview for</p>
            <h1 className="text-2xl font-semibold tracking-[-0.01em] text-white">{d.job_title}</h1>
          </div>
          <div className="flex items-start gap-2 rounded-lg bg-white/5 px-3 py-2 text-sm text-slate-300">
            <CalendarClock aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-200" />
            <span>
              {formatWhen(d.scheduled_start, zone)} <span className="text-slate-400">your time</span>
              {zone !== d.timezone_name && (
                <span className="block text-xs text-slate-400">
                  {formatWhen(d.scheduled_start, d.timezone_name)} for the interviewer
                </span>
              )}
            </span>
          </div>
        </div>

        <div className="mt-6">
          {phase === 'check' && (
            <PreJoin
              canJoin={open}
              joinLabel="Join the interview"
              busy={join.isPending}
              error={
                join.error
                  ? join.error instanceof ApiError
                    ? join.error.message
                    : "Couldn't connect to the video call. Check your connection and try again."
                  : null
              }
              waiting={`Waiting for ${interviewer} to open the room. You'll be able to join here as soon as they do.`}
              onJoin={(media) => join.mutate(media)}
            />
          )}
          {phase === 'call' && (
            <CallStage
              call={call}
              otherLabel={d.interviewer_name ?? 'Interviewer'}
              waitingFor={`Waiting for ${interviewer}.`}
              onLeave={() => void call.leave()}
            />
          )}
          {phase === 'left' && (
            <div className="flex min-h-[280px] flex-col items-center justify-center gap-4 rounded-2xl bg-slate-800/40 px-4 text-center">
              <h2 className="text-xl font-semibold">
                {call.replaced ? 'You joined from another tab or device' : call.leftByUs ? 'You left the call' : 'The connection was lost'}
              </h2>
              <p className="text-sm text-slate-300">The interview is still on. You can go back in.</p>
              <button
                type="button"
                onClick={() => {
                  join.reset()
                  setPhase('check')
                }}
                className="rounded-md bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600"
              >
                Rejoin
              </button>
            </div>
          )}
        </div>

        <p className="mx-auto mt-10 max-w-lg text-center text-xs leading-relaxed text-slate-400">
          What is said in the interview is turned into a written transcript for the hiring team.
          The audio and video are not recorded. This link is personal to you, please don't share it.
          If the time no longer works, reply to the invitation email.
        </p>
      </div>
    </Shell>
  )
}

function Shell({ jobTitle, children }: { jobTitle?: string; children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col bg-slate-900 text-white">
      <header className="flex items-center justify-between border-b border-white/10 px-4 py-3 sm:px-6">
        <p className="text-base font-semibold">
          Intelli<span className="text-marker">Hire</span>
        </p>
        {jobTitle && <p className="truncate pl-4 text-sm text-slate-400">Interview room</p>}
      </header>
      {children}
    </main>
  )
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-1 items-center justify-center px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold text-white">{title}</h1>
        <p className="mt-2 text-sm text-slate-300">{children}</p>
      </div>
    </div>
  )
}
