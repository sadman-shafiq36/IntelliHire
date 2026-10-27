// the interviewer's room: camera check, the call, and the live transcript beside it.
// opening the room starts the interview; leaving doesn't end it, so a dropped
// connection or a reload rejoins the same session with its transcript intact

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CircleStop, FileText } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { CallConnection, SessionDetails, Transcript, TranscriptTurn } from '@/api/types'
import { Spinner } from '@/components/ui'
import { type Media, useCall } from '@/hooks/useCall'
import { cn } from '@/lib/cn'
import { CallStage } from '@/pages/CallStage'
import { AskPanel, QuestionsPanel } from '@/pages/CopilotPanel'
import { IdentityBanner, ProfilePanel } from '@/pages/IdentityPanels'
import { PreJoin } from '@/pages/PreJoin'
import { elapsed, mergeTurns, TranscriptLines } from '@/pages/TranscriptLines'

type Phase = 'check' | 'call' | 'left'

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}

export function InterviewRoomPage() {
  const { interviewId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [phase, setPhase] = useState<Phase>('check')
  const [pushed, setPushed] = useState<TranscriptTurn[]>([])
  const [confirmEnd, setConfirmEnd] = useState(false)

  const onData = useCallback((topic: string | undefined, msg: unknown) => {
    const m = msg as { type?: string; turn?: TranscriptTurn }
    if (topic === 'transcript' && m.type === 'turn' && m.turn) setPushed((p) => [...p, m.turn!])
    // the transcriber looked at the candidate's camera; the banner and profile re-read
    if (topic === 'identity') {
      void queryClient.invalidateQueries({ queryKey: ['identity', Number(interviewId)] })
      void queryClient.invalidateQueries({ queryKey: ['profile', Number(interviewId)] })
    }
  }, [queryClient, interviewId])
  const call = useCall(onData)

  const session = useQuery({
    queryKey: ['session', interviewId],
    queryFn: () => api.get<SessionDetails>(`/api/v1/interviews/${interviewId}/session`),
    refetchInterval: 5000,
  })
  const live = session.data?.status === 'in_progress'

  const transcript = useQuery({
    queryKey: ['transcript', interviewId],
    queryFn: () => api.get<Transcript>(`/api/v1/interviews/${interviewId}/transcript`),
    // lines come in over the call; this catches anything missed while reconnecting
    refetchInterval: live ? 10_000 : false,
  })

  const join = useMutation({
    mutationFn: async (media: Media) => {
      const conn = await api.post<CallConnection>(`/api/v1/interviews/${interviewId}/session`)
      await call.join(conn, media)
    },
    onSuccess: () => {
      setPhase('call')
      void queryClient.invalidateQueries({ queryKey: ['session', interviewId] })
    },
  })

  const end = useMutation({
    mutationFn: () => api.post<SessionDetails>(`/api/v1/interviews/${interviewId}/session/end`),
    onSuccess: async (ended) => {
      await call.leave()
      // the transcript page reads these; without this it shows the cached live state
      queryClient.setQueryData(['session', interviewId], ended)
      void queryClient.invalidateQueries({ queryKey: ['transcript', interviewId] })
      void queryClient.invalidateQueries({ queryKey: ['interviews'] })
      navigate(`/interviews/${interviewId}/transcript`)
    },
  })

  // a drop the sdk couldn't recover from, or the room was closed elsewhere
  useEffect(() => {
    if (phase === 'call' && call.state === 'ended') setPhase('left')
  }, [phase, call.state])

  const d = session.data
  const turns = mergeTurns(transcript.data?.turns ?? [], pushed)

  if (session.isLoading)
    return (
      <Shell>
        <div className="flex flex-1 items-center justify-center text-slate-300">
          <Spinner label="Opening the interview room" />
        </div>
      </Shell>
    )
  if (session.error || !d)
    return (
      <Shell>
        <Centered title="This interview can't be opened">
          {message(session.error, 'Please go back and try again.')}
        </Centered>
      </Shell>
    )

  const endButton = (
    <EndControl
      confirming={confirmEnd}
      pending={end.isPending}
      onAsk={() => setConfirmEnd(true)}
      onCancel={() => setConfirmEnd(false)}
      onConfirm={() => end.mutate()}
    />
  )

  return (
    <Shell session={d}>
      <div className="mx-auto grid w-full max-w-7xl flex-1 gap-6 px-4 py-6 lg:grid-cols-[minmax(0,1fr)_23.75rem]">
        <div className="min-w-0">
          {d.status === 'completed' ? (
            <Centered title="This interview has ended">
              <Link className="font-medium text-brand-200 underline" to={`/interviews/${d.id}/transcript`}>
                Read the transcript
              </Link>
            </Centered>
          ) : phase === 'check' ? (
            <PreJoin
              canJoin={d.status === 'scheduled' || d.status === 'in_progress'}
              joinLabel={d.status === 'in_progress' ? 'Rejoin the interview' : 'Start the interview'}
              busy={join.isPending}
              error={join.error ? message(join.error, "Couldn't connect to the video service. Check your connection and try again.") : null}
              waiting="This interview can't be started in its current state."
              onJoin={(media) => join.mutate(media)}
            />
          ) : phase === 'call' ? (
            <>
              <IdentityBanner session={d} />
              <CallStage
                call={call}
                otherLabel={d.candidate_name}
                waitingFor={`Waiting for ${d.candidate_name} to join. They join from the link in their invitation.`}
                onLeave={() => void call.leave()}
                extraControls={endButton}
              />
            </>
          ) : (
            <Centered title={call.replaced ? 'You joined from another tab' : call.leftByUs ? 'You left the call' : 'The connection was lost'}>
              <p>The interview is still running and its transcript is kept.</p>
              <div className="mt-4 flex flex-wrap justify-center gap-3">
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
                {endButton}
              </div>
            </Centered>
          )}
          {end.error && (
            <p role="alert" className="mt-3 text-center text-sm text-red-200">
              {message(end.error, 'The interview could not be ended. Try again.')}
            </p>
          )}
        </div>

        <SidePanel turns={turns} candidateName={d.candidate_name} session={d} />
      </div>
    </Shell>
  )
}

type Tab = 'transcript' | 'profile' | 'questions' | 'ask'

const TABS: [Tab, string][] = [
  ['transcript', 'Transcript'],
  ['profile', 'Profile'],
  ['questions', 'Questions'],
  ['ask', 'Ask the co-pilot'],
]

// all stay mounted, so switching keeps the transcript's scroll and a reply streaming
function SidePanel({
  turns,
  candidateName,
  session,
}: {
  turns: TranscriptTurn[]
  candidateName: string
  session: SessionDetails
}) {
  const [tab, setTab] = useState<Tab>('transcript')
  return (
    <aside
      aria-label="Transcript and co-pilot"
      className="flex h-[75vh] min-h-[22.5rem] flex-col overflow-hidden rounded-2xl bg-slate-800/70 lg:h-[calc(100vh-7rem)]"
    >
      <div role="tablist" aria-label="Side panel" className="flex shrink-0 border-b border-white/10">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            id={`tab-${id}`}
            type="button"
            role="tab"
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            onClick={() => setTab(id)}
            className={cn(
              'flex-1 border-b-2 px-2 py-2.5 text-xs font-medium transition',
              tab === id ? 'border-marker text-white' : 'border-transparent text-slate-300 hover:text-white',
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {TABS.map(([id]) => (
        <div
          key={id}
          id={`panel-${id}`}
          role="tabpanel"
          aria-labelledby={`tab-${id}`}
          hidden={tab !== id}
          className="min-h-0 flex-1"
        >
          {id === 'transcript' ? (
            <TranscriptPanel turns={turns} candidateName={candidateName} session={session} />
          ) : id === 'profile' ? (
            <ProfilePanel session={session} />
          ) : id === 'questions' ? (
            <QuestionsPanel session={session} />
          ) : (
            <AskPanel session={session} />
          )}
        </div>
      ))}
    </aside>
  )
}

function EndControl({
  confirming,
  pending,
  onAsk,
  onCancel,
  onConfirm,
}: {
  confirming: boolean
  pending: boolean
  onAsk: () => void
  onCancel: () => void
  onConfirm: () => void
}) {
  if (!confirming)
    return (
      <button
        type="button"
        onClick={onAsk}
        className="inline-flex h-12 items-center gap-2 rounded-full bg-white/10 px-4 text-sm font-medium text-white hover:bg-white/20"
      >
        <CircleStop aria-hidden="true" className="h-4 w-4" />
        End interview
      </button>
    )
  return (
    <span className="inline-flex flex-wrap items-center gap-2 rounded-full bg-white/10 py-1.5 pl-4 pr-1.5 text-sm text-white">
      End it for both of you?
      <button
        type="button"
        onClick={onConfirm}
        disabled={pending}
        className="rounded-full bg-red-700 px-3 py-1.5 font-medium hover:bg-red-800 disabled:opacity-50"
      >
        {pending ? 'Ending…' : 'End interview'}
      </button>
      <button type="button" onClick={onCancel} className="rounded-full px-3 py-1.5 hover:bg-white/10">
        Keep going
      </button>
    </span>
  )
}

function TranscriptPanel({
  turns,
  candidateName,
  session,
}: {
  turns: TranscriptTurn[]
  candidateName: string
  session: SessionDetails
}) {
  const box = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)
  const count = turns.length

  // follow new lines unless the interviewer has scrolled up to read something
  useEffect(() => {
    if (atBottom && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [count, atBottom])

  const t = session.transcription
  return (
    <section aria-labelledby="live-transcript" className="flex h-full flex-col">
      <div className="border-b border-white/10 px-4 py-3">
        <h2 id="live-transcript" className="text-sm font-semibold text-white">
          Live transcript
        </h2>
        <p className="mt-1 flex items-center gap-2 text-xs text-slate-300" aria-live="polite">
          <span
            aria-hidden="true"
            className={cn(
              'h-2 w-2 shrink-0 rounded-full',
              t.state === 'running' ? 'bg-brand-500' : t.state === 'degraded' ? 'bg-amber-400' : 'bg-slate-500',
            )}
          />
          {t.message || (session.status === 'scheduled' ? 'Starts when you open the room.' : 'Not running.')}
        </p>
        {(t.backlog ?? 0) > 1 && (
          <p className="mt-1 text-xs text-amber-200">
            Catching up: {t.backlog} pieces of speech are waiting. Nothing is lost; they appear shortly.
          </p>
        )}
      </div>
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget
          setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
        }}
        className="relative flex-1 overflow-y-auto px-4 py-3"
      >
        {count === 0 ? (
          <p className="text-sm text-slate-400">
            What each of you says appears here a moment after you pause.
          </p>
        ) : (
          <TranscriptLines turns={turns} candidateName={candidateName} timeZone={session.timezone_name} dark />
        )}
      </div>
      {!atBottom && (
        <button
          type="button"
          onClick={() => setAtBottom(true)}
          className="border-t border-white/10 py-2 text-xs font-medium text-brand-200 hover:bg-white/5"
        >
          Jump to the latest
        </button>
      )}
      <Link
        to={`/interviews/${session.id}/transcript`}
        className="flex items-center gap-2 border-t border-white/10 px-4 py-2 text-xs text-slate-300 hover:bg-white/5"
        target="_blank"
      >
        <FileText aria-hidden="true" className="h-3.5 w-3.5" />
        Open the full transcript in a new tab
      </Link>
    </section>
  )
}

function Clock({ since }: { since: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return <span className="tabular">{elapsed(now - new Date(since).getTime())}</span>
}

function Shell({ session, children }: { session?: SessionDetails; children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col bg-slate-900 text-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          {session && (
            <Link
              to={`/jobs/${session.job_id}?tab=interviews`}
              className="rounded-md p-1 text-slate-300 hover:bg-white/10 hover:text-white"
              aria-label="Back to the job's interviews"
            >
              <ArrowLeft aria-hidden="true" className="h-5 w-5" />
            </Link>
          )}
          <div className="min-w-0">
            <p className="truncate text-base font-semibold">
              {session ? `Interview with ${session.candidate_name}` : (
                <>Intelli<span className="text-marker">Hire</span></>
              )}
            </p>
            {session && <p className="truncate text-sm text-slate-400">{session.job_title}</p>}
          </div>
        </div>
        {session?.status === 'in_progress' && session.started_at && (
          <p className="flex items-center gap-2 text-sm text-slate-300">
            <span aria-hidden="true" className="h-2 w-2 rounded-full bg-red-500" />
            In progress <Clock since={session.started_at} />
          </p>
        )}
      </header>
      {children}
    </main>
  )
}

function Centered({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex min-h-[20rem] items-center justify-center rounded-2xl bg-slate-800/40 px-4 py-10">
      <div className="max-w-md text-center">
        <h2 className="text-xl font-semibold text-white">{title}</h2>
        {children && <div className="mt-2 text-sm text-slate-300">{children}</div>}
      </div>
    </div>
  )
}
