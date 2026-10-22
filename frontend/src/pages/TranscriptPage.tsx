// the stored transcript of one interview, with the session record: when it ran and
// who was in the room. readable while the interview is still going

import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ListChecks, Search, Video } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { SessionDetails, Transcript } from '@/api/types'
import { Alert, Badge, buttonClass, Card, EmptyState, inputClass, PageHeader, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'
import { formatWhen } from '@/pages/InterviewsPanel'
import { clockTime, elapsed, mergeTurns, TranscriptLines } from '@/pages/TranscriptLines'

type Who = 'all' | 'interviewer' | 'candidate'

const STATUS_LABEL: Partial<Record<SessionDetails['status'], string>> = {
  scheduled: 'Not started',
  in_progress: 'In progress',
  completed: 'Ended',
  cancelled: 'Cancelled',
}

export function TranscriptPage() {
  const { interviewId = '' } = useParams()
  const [who, setWho] = useState<Who>('all')
  const [search, setSearch] = useState('')

  const session = useQuery({
    queryKey: ['session', interviewId],
    queryFn: () => api.get<SessionDetails>(`/api/v1/interviews/${interviewId}/session`),
  })
  const live = session.data?.status === 'in_progress'
  const transcript = useQuery({
    queryKey: ['transcript', interviewId],
    queryFn: () => api.get<Transcript>(`/api/v1/interviews/${interviewId}/transcript`),
    refetchInterval: live ? 5000 : false,
  })

  if (session.isLoading || transcript.isLoading) return <Spinner label="Loading the transcript" />
  const error = session.error ?? transcript.error
  if (error || !session.data || !transcript.data)
    return <Alert tone="error">{error instanceof ApiError ? error.message : 'Could not load the transcript.'}</Alert>

  const d = session.data
  const all = mergeTurns(transcript.data.turns)
  const needle = search.trim().toLowerCase()
  const shown = all.filter(
    (t) => (who === 'all' || t.speaker_role === who) && (!needle || t.text.toLowerCase().includes(needle)),
  )
  const duration =
    d.started_at && (d.ended_at || live)
      ? elapsed((d.ended_at ? new Date(d.ended_at).getTime() : Date.now()) - new Date(d.started_at).getTime())
      : null
  const unclear = all.filter((t) => t.is_uncertain).length

  return (
    <div>
      <PageHeader
        back={
          <Link to={`/jobs/${d.job_id}?tab=interviews`} className="inline-flex items-center gap-1 text-slate-600 hover:text-slate-900">
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            {d.job_title}
          </Link>
        }
        title={`Transcript: ${d.candidate_name}`}
        description={
          d.started_at
            ? `Interview on ${formatWhen(d.started_at, d.timezone_name)}. Times are in ${d.timezone_name}.`
            : 'Not started yet.'
        }
        actions={
          live ? (
            <Link to={`/interviews/${d.id}/room`} className={buttonClass({})}>
              <Video aria-hidden="true" className="h-4 w-4" />
              Rejoin the room
            </Link>
          ) : (
            d.status === 'completed' && (
              <Link to={`/interviews/${d.id}/analysis`} className={buttonClass({})}>
                <ListChecks aria-hidden="true" className="h-4 w-4" />
                Q&amp;A analysis
              </Link>
            )
          )
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section aria-labelledby="transcript-heading" className="min-w-0 rounded-xl border border-slate-200 bg-white p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="transcript-heading" className="text-base font-semibold text-slate-900">
              {all.length} {all.length === 1 ? 'turn' : 'turns'}
              {live && <span className="ml-2 align-middle"><Badge tone="info">Live</Badge></span>}
            </h2>
            <div className="flex flex-wrap items-center gap-2">
              <div role="group" aria-label="Speaker" className="flex rounded-md border border-slate-300 p-0.5 text-sm">
                {(['all', 'interviewer', 'candidate'] as const).map((w) => (
                  <button
                    key={w}
                    type="button"
                    aria-pressed={who === w}
                    onClick={() => setWho(w)}
                    className={cn('rounded px-2.5 py-1', who === w ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100')}
                  >
                    {w === 'all' ? 'Both' : w === 'interviewer' ? 'Interviewer' : 'Candidate'}
                  </button>
                ))}
              </div>
              <label className="relative block w-48">
                <span className="sr-only">Search the transcript</span>
                <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search"
                  className={cn(inputClass, 'border-slate-300 pl-8')}
                />
              </label>
            </div>
          </div>
          <div className="mt-5">
            {all.length === 0 ? (
              <EmptyState title="Nothing transcribed yet">
                {live ? 'Lines appear here as the interview goes on.' : 'No speech was transcribed in this interview.'}
              </EmptyState>
            ) : shown.length === 0 ? (
              <p className="text-sm text-slate-600">No lines match.</p>
            ) : (
              <TranscriptLines turns={shown} candidateName={d.candidate_name} timeZone={d.timezone_name} />
            )}
          </div>
        </section>

        <div className="space-y-6">
          <Card title="Session">
            <dl className="space-y-2 text-sm">
              <Row label="Status">{STATUS_LABEL[d.status] ?? d.status}</Row>
              <Row label="Scheduled">{formatWhen(d.scheduled_start, d.timezone_name)}</Row>
              <Row label="Started">{d.started_at ? clockTime(d.started_at, d.timezone_name) : '—'}</Row>
              <Row label="Ended">{d.ended_at ? clockTime(d.ended_at, d.timezone_name) : '—'}</Row>
              <Row label="Length">{duration ?? '—'}</Row>
              {unclear > 0 && <Row label="Unclear lines">{unclear}</Row>}
            </dl>
          </Card>
          <Card title="Who was in the room" description="Each connection is listed, so a reconnect shows twice.">
            {d.attendance.length === 0 ? (
              <p className="text-sm text-slate-600">Nobody has joined yet.</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {d.attendance.map((a, i) => (
                  <li key={i} className="flex justify-between gap-3">
                    <span className="text-slate-900">{a.role === 'interviewer' ? 'Interviewer' : d.candidate_name}</span>
                    <span className="tabular text-slate-600">
                      {clockTime(a.joined_at, d.timezone_name)}–{a.left_at ? clockTime(a.left_at, d.timezone_name) : 'now'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-slate-600">{label}</dt>
      <dd className="tabular text-right text-slate-900">{children}</dd>
    </div>
  )
}
