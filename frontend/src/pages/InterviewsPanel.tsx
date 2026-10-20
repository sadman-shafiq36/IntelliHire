// interviews step: book the candidates selected in scoring, then move or cancel them.
// the scheduling agent does the booking and the email in the background

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, CalendarPlus, CalendarX2, FileText, Mail, RotateCcw, Video } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { InterviewRow, JobInterviews, ScheduleOptions } from '@/api/types'
import { Alert, Badge, Button, buttonClass, EmptyState, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { ScheduleDialog, type DialogMode } from '@/pages/ScheduleDialog'

const STATUS: Record<InterviewRow['status'], { label: string; tone: 'success' | 'warning' | 'neutral' | 'error' | 'info' }> = {
  requested: { label: 'Booking', tone: 'info' },
  scheduled: { label: 'Scheduled', tone: 'success' },
  failed: { label: 'Not booked', tone: 'error' },
  in_progress: { label: 'In progress', tone: 'info' },
  completed: { label: 'Done', tone: 'neutral' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
}

export function formatWhen(iso: string | null, timeZone: string): string {
  if (!iso) return 'Time not set'
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso))
}

export function InterviewsPanel({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<DialogMode | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const progress = useBackgroundJob(runId)

  const data = useQuery({
    queryKey: ['interviews', jobId],
    queryFn: () => api.get<JobInterviews>(`/api/v1/jobs/${jobId}/interviews`),
    // a booking someone else started keeps this list moving
    refetchInterval: (q) =>
      q.state.data?.interviews.some((i) => i.pending_action) ? 3000 : false,
  })

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['interviews', jobId] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
    void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }

  useEffect(() => {
    if (isDone(progress.data)) refresh()
    // refresh only depends on the job id and the query client
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.state])

  const submit = useMutation({
    mutationFn: async ({ mode, options, reason }: { mode: DialogMode; options?: ScheduleOptions; reason?: string }) => {
      if (mode.kind === 'schedule')
        return api.post<{ background_job_id: string | null }>(`/api/v1/jobs/${jobId}/interviews`, {
          candidate_id: mode.candidateId,
          ...options,
        })
      if (mode.kind === 'cancel')
        return api.post<{ background_job_id: string | null }>(`/api/v1/interviews/${mode.interview.id}/cancel`, { reason })
      return api.post<{ background_job_id: string | null }>(
        `/api/v1/interviews/${mode.interview.id}/reschedule`,
        options,
      )
    },
    onSuccess: (res) => {
      setDialog(null)
      setRunId(res.background_job_id)
      refresh()
    },
  })

  const d = data.data
  const running = runId !== null && !isDone(progress.data)
  const active = d?.interviews.filter((i) => ['requested', 'scheduled', 'in_progress', 'failed'].includes(i.status)) ?? []
  const past = d?.interviews.filter((i) => ['cancelled', 'completed'].includes(i.status)) ?? []

  return (
    <div className="space-y-6">
      {data.isLoading && <Spinner label="Loading interviews" />}
      {data.error && (
        <Alert tone="error">{data.error instanceof ApiError ? data.error.message : 'Could not load.'}</Alert>
      )}

      {d && <ChannelNote data={d} />}

      {running && progress.data && (
        <div className="rounded-xl border border-slate-200 bg-white p-5" aria-live="polite">
          <Spinner label={progress.data.message || 'Working on it'} />
        </div>
      )}
      {progress.data?.state === 'failed' && <Alert tone="error">{progress.data.message}</Alert>}

      {d && d.to_schedule.length === 0 && d.interviews.length === 0 && (
        <EmptyState icon={<CalendarClock className="h-8 w-8" aria-hidden="true" />} title="Nobody to interview yet">
          Select candidates for interview in the Scoring step. They appear here, ready to schedule.
        </EmptyState>
      )}

      {d && d.to_schedule.length > 0 && (
        <section aria-labelledby="ready-heading">
          <h2 id="ready-heading" className="text-lg font-semibold text-slate-900">
            Ready to schedule <span className="tabular font-normal text-slate-500">({d.to_schedule.length})</span>
          </h2>
          <ul className="mt-3 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {d.to_schedule.map((c) => (
              <li key={c.candidate_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4">
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-slate-900">{c.full_name}</span>
                  <span className="block text-sm text-slate-600">
                    {c.rank ? `#${c.rank} by score, ` : ''}
                    {Math.round(c.aggregate_score)} of 100
                    {!c.has_email && ' · no email address, so no invitation can be sent'}
                  </span>
                </span>
                <Button
                  size="sm"
                  disabled={!c.has_email || running}
                  onClick={() => setDialog({ kind: 'schedule', candidateId: c.candidate_id, name: c.full_name })}
                >
                  <CalendarPlus aria-hidden="true" className="h-4 w-4" />
                  Schedule
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {active.length > 0 && (
        <section aria-labelledby="booked-heading">
          <h2 id="booked-heading" className="text-lg font-semibold text-slate-900">
            Interviews <span className="tabular font-normal text-slate-500">({active.length})</span>
          </h2>
          <ul className="mt-3 space-y-3">
            {active.map((i) => (
              <InterviewItem
                key={i.id}
                interview={i}
                busy={running}
                onReschedule={() => setDialog({ kind: 'reschedule', interview: i })}
                onCancel={() => setDialog({ kind: 'cancel', interview: i })}
              />
            ))}
          </ul>
        </section>
      )}

      {past.length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-white px-5 py-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            Done and cancelled ({past.length})
          </summary>
          <ul className="mt-2 divide-y divide-slate-100">
            {past.map((i) => (
              <li key={i.id} className="py-2 text-sm text-slate-700">
                {i.full_name}, {formatWhen(i.scheduled_start, i.timezone_name)}{' '}
                <Badge tone={STATUS[i.status].tone}>{STATUS[i.status].label}</Badge>
                {i.cancellation_reason && <span className="text-slate-500"> · {i.cancellation_reason}</span>}
                {i.status === 'completed' && (
                  <Link to={`/interviews/${i.id}/transcript`} className="ml-2 font-medium text-brand-700 underline">
                    Transcript
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      {dialog && (
        <ScheduleDialog
          mode={dialog}
          pending={submit.isPending}
          error={submit.error instanceof ApiError ? submit.error.message : submit.error ? 'That did not work.' : null}
          onClose={() => {
            setDialog(null)
            submit.reset()
          }}
          onSubmit={(options, reason) => submit.mutate({ mode: dialog, options, reason })}
        />
      )}
    </div>
  )
}

function ChannelNote({ data }: { data: JobInterviews }) {
  if (data.channel === 'google')
    return (
      <p className="flex items-start gap-2 text-sm text-slate-600">
        <Mail aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" />
        <span>
          Invitations are sent from <span className="font-medium text-slate-900">{data.account}</span>{' '}
          through Gmail, and each interview goes on that Google Calendar. The agent checks your
          calendar before it books a time. The video call itself is held in IntelliHire.
        </span>
      </p>
    )
  return (
    <Alert tone="info">
      {data.needs_reconnect
        ? 'Google is connected without Gmail and Calendar access. '
        : 'Google is not connected. '}
      Invitations go to the local mail catcher (MailHog, <code>localhost:8025</code>) with a calendar
      file, and your availability is not checked.{' '}
      <Link to="/settings/integrations" className="font-medium underline">
        {data.needs_reconnect ? 'Reconnect Google' : 'Connect Google'}
      </Link>{' '}
      to send real invitations.
    </Alert>
  )
}

function InterviewItem({
  interview: i,
  busy,
  onReschedule,
  onCancel,
}: {
  interview: InterviewRow
  busy: boolean
  onReschedule: () => void
  onCancel: () => void
}) {
  const working = !!i.pending_action
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
            {i.full_name}
            <Badge tone={STATUS[i.status].tone}>{STATUS[i.status].label}</Badge>
          </p>
          <p className="mt-1 text-sm text-slate-700">
            {i.status === 'scheduled'
              ? `${formatWhen(i.scheduled_start, i.timezone_name)} · ${i.duration_minutes} min · ${i.timezone_name}`
              : i.status === 'in_progress'
                ? `Started ${formatWhen(i.started_at, i.timezone_name)}. The room is open until you end the interview.`
                : working
                ? 'The agent is booking it.'
                : 'Nothing was booked or sent.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {i.status === 'scheduled' && !working && (
            <Link to={`/interviews/${i.id}/room`} className={buttonClass({ size: 'sm' })}>
              <Video aria-hidden="true" className="h-4 w-4" />
              Start interview
            </Link>
          )}
          {i.status === 'in_progress' && (
            <>
              <Link to={`/interviews/${i.id}/room`} className={buttonClass({ size: 'sm' })}>
                <Video aria-hidden="true" className="h-4 w-4" />
                Rejoin
              </Link>
              <Link to={`/interviews/${i.id}/transcript`} className={buttonClass({ size: 'sm', variant: 'outline' })}>
                <FileText aria-hidden="true" className="h-4 w-4" />
                Transcript
              </Link>
            </>
          )}
          {i.status === 'scheduled' && (
            <>
              <Button size="sm" variant="outline" disabled={working || busy} onClick={onReschedule}>
                <CalendarClock aria-hidden="true" className="h-4 w-4" />
                Reschedule
              </Button>
              <Button size="sm" variant="ghost" disabled={working || busy} onClick={onCancel}>
                <CalendarX2 aria-hidden="true" className="h-4 w-4" />
                Cancel
              </Button>
            </>
          )}
          {i.status === 'failed' && !working && (
            <>
              <Button size="sm" disabled={busy} onClick={onReschedule}>
                <RotateCcw aria-hidden="true" className="h-4 w-4" />
                Try again
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
                Dismiss
              </Button>
            </>
          )}
        </div>
      </div>

      {i.status === 'scheduled' && (
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span className="inline-flex items-center gap-1.5 text-slate-600">
            <Video aria-hidden="true" className="h-4 w-4" />
            Video call in IntelliHire
          </span>
          <span className="inline-flex items-center gap-1.5 text-slate-600">
            <Mail aria-hidden="true" className="h-4 w-4" />
            {i.invitation_channel === 'gmail' ? 'Invited through Gmail' : 'Invitation in MailHog'}
            {i.has_calendar_event && ', on your Google Calendar'}
          </span>
        </div>
      )}
      {working && i.status === 'scheduled' && (
        <p className="mt-3 text-sm text-slate-600" aria-live="polite">
          {i.pending_action === 'cancel' ? 'Cancelling…' : 'Moving it…'}
        </p>
      )}
      {i.last_error && !working && (
        <p className={`mt-3 text-sm ${i.status === 'failed' ? 'text-red-800' : 'text-slate-600'}`}>
          {i.last_error}
        </p>
      )}
    </li>
  )
}
