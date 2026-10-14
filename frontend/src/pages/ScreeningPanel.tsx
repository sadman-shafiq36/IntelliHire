// screening step: run it, see the ranking, who was excluded and why, and what couldn't be read

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, FileX2, RefreshCw, ScanText, UserX } from 'lucide-react'
import { useEffect, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type { RankedCandidate, ScreeningResults } from '@/api/types'
import { Alert, Button, EmptyState, ProgressBar, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'
import { CandidateEvidence } from '@/pages/CandidateEvidence'

export function ScreeningPanel({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient()
  const [runId, setRunId] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const progress = useBackgroundJob(runId)

  const results = useQuery({
    queryKey: ['screening', jobId],
    queryFn: () => api.get<ScreeningResults>(`/api/v1/jobs/${jobId}/screening`),
  })

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['screening', jobId] })
    void queryClient.invalidateQueries({ queryKey: ['job-cvs', jobId] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
    void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }

  // keep the list filling in while the batch runs, and once more at the end
  useEffect(() => {
    if (progress.data) refresh()
    // refresh only depends on the job id and the query client
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.completed, progress.data?.state])

  const start = useMutation({
    mutationFn: (rescreen: boolean) =>
      api.post<{ background_job_id: string; cv_count: number }>(
        `/api/v1/jobs/${jobId}/screening`,
        { rescreen },
      ),
    onSuccess: (data) => setRunId(data.background_job_id),
  })

  const running = runId !== null && !isDone(progress.data)
  const data = results.data
  const pending = data?.pending_count ?? 0
  const screenedAny = !!data && data.ranked.length + data.excluded.length > 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="max-w-2xl text-sm text-slate-600">
          Each CV is read, compared with every requirement by meaning rather than keywords, and
          ranked by how much of the role it covers. Every judgement quotes the line of the CV it
          is based on. A missing must-have rules a candidate out.
        </p>
        <div className="flex gap-2">
          {screenedAny && (
            <Button
              variant="outline"
              disabled={running || start.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    'Screen every CV again? Do this after changing the requirements. It takes a few minutes.',
                  )
                )
                  start.mutate(true)
              }}
            >
              <RefreshCw aria-hidden="true" className="h-4 w-4" />
              Screen all again
            </Button>
          )}
          <Button disabled={running || start.isPending || pending === 0} onClick={() => start.mutate(false)}>
            <ScanText aria-hidden="true" className="h-4 w-4" />
            {pending > 0 ? `Screen ${pending} CV${pending === 1 ? '' : 's'}` : 'No CVs waiting'}
          </Button>
        </div>
      </div>

      {start.error && (
        <Alert tone="error">
          {start.error instanceof ApiError ? start.error.message : 'Screening could not start.'}
        </Alert>
      )}

      {running && progress.data && (
        <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-5" aria-live="polite">
          <div className="flex items-center justify-between gap-3 text-sm">
            <Spinner label={progress.data.message || 'Screening'} />
            {progress.data.total > 0 && (
              <span className="tabular text-slate-600">
                {progress.data.completed} of {progress.data.total}
              </span>
            )}
          </div>
          <ProgressBar percent={progress.data.percent} />
          <p className="text-xs text-slate-500">
            About 30 seconds per CV, a few at a time. You can leave this page; it carries on.
          </p>
        </div>
      )}
      {progress.data?.state === 'failed' && <Alert tone="error">{progress.data.message}</Alert>}

      {results.isLoading && <Spinner label="Loading results" />}

      {data && !screenedAny && !running && (
        <EmptyState
          icon={<ScanText className="h-8 w-8" aria-hidden="true" />}
          title={pending > 0 ? 'Ready to screen' : 'No CVs yet'}
        >
          {pending > 0
            ? `${pending} CV${pending === 1 ? ' is' : 's are'} waiting. Screening ranks them against this job's requirements.`
            : 'Sync applications or upload CVs in the Applications step, then screen them here.'}
        </EmptyState>
      )}

      {data && data.ranked.length > 0 && (
        <section aria-labelledby="ranked-heading">
          <h2 id="ranked-heading" className="text-lg font-semibold text-slate-900">
            Ranking <span className="tabular font-normal text-slate-500">({data.ranked.length})</span>
          </h2>
          <ol className="mt-3 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {data.ranked.map((c) => (
              <li key={c.candidate_id}>
                <CandidateRow candidate={c} onOpen={() => setOpen(c.candidate_id)} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {data && data.excluded.length > 0 && (
        <section aria-labelledby="excluded-heading">
          <h2 id="excluded-heading" className="flex items-center gap-2 text-lg font-semibold text-slate-900">
            <UserX aria-hidden="true" className="h-5 w-5 text-slate-500" />
            Ruled out by a must-have{' '}
            <span className="tabular font-normal text-slate-500">({data.excluded.length})</span>
          </h2>
          <ul className="mt-3 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {data.excluded.map((c) => (
              <li key={c.candidate_id}>
                <button
                  type="button"
                  onClick={() => setOpen(c.candidate_id)}
                  className="flex w-full items-start gap-4 px-5 py-3.5 text-left transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium text-slate-900">{c.full_name}</span>
                    <span className="mt-0.5 block text-sm text-slate-600">{c.exclusion_reason}</span>
                  </span>
                  <span className="tabular shrink-0 text-sm text-slate-500">
                    {Math.round(c.fulfilment_percent)}%
                  </span>
                  <ChevronRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && data.not_screened.length > 0 && (
        <section aria-labelledby="unread-heading">
          <h2 id="unread-heading" className="flex items-center gap-2 text-lg font-semibold text-slate-900">
            <FileX2 aria-hidden="true" className="h-5 w-5 text-slate-500" />
            Could not be screened{' '}
            <span className="tabular font-normal text-slate-500">({data.not_screened.length})</span>
          </h2>
          <ul className="mt-3 space-y-2">
            {data.not_screened.map((n) => (
              <li key={n.cv_document_id} className="rounded-lg border border-slate-200 bg-white px-4 py-3">
                <p className="text-sm font-medium text-slate-900">{n.candidate_name ?? n.filename}</p>
                <p className="text-sm text-slate-600">{n.reason}</p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-500">
            CVs that hit a busy AI service are included the next time you screen. The others need
            a person: a damaged file can be asked for again, and a CV in another language can be
            reviewed by someone who reads it.
          </p>
        </section>
      )}

      {open !== null && (
        <CandidateEvidence jobId={jobId} candidateId={open} onClose={() => setOpen(null)} />
      )}
    </div>
  )
}

function CandidateRow({ candidate: c, onOpen }: { candidate: RankedCandidate; onOpen: () => void }) {
  const total = c.met + c.partially_met + c.unmet || 1
  const mustOk = c.must_have_met === c.must_have_total
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group grid w-full grid-cols-[2.5rem_minmax(0,1fr)] items-center gap-x-4 gap-y-2 px-5 py-4 text-left transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none sm:grid-cols-[2.5rem_minmax(0,1fr)_220px_auto]"
    >
      <span className="tabular text-2xl font-semibold tracking-[-0.02em] text-slate-900">{c.rank}</span>
      <span className="min-w-0">
        <span className="block truncate font-medium text-slate-900 group-hover:underline">
          {c.full_name}
        </span>
        <span className="block truncate text-sm text-slate-600">
          {[c.headline, c.years_experience ? `${c.years_experience} years` : null]
            .filter(Boolean)
            .join(', ')}
        </span>
      </span>
      <span className="col-span-2 sm:col-span-1">
        <span className="flex items-center gap-3">
          <span className="flex h-2 flex-1 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
            <span className="bg-brand-600" style={{ width: `${(c.met / total) * 100}%` }} />
            <span className="bg-amber-400" style={{ width: `${(c.partially_met / total) * 100}%` }} />
          </span>
          <span className="tabular w-12 text-right text-sm font-semibold text-slate-900">
            {Math.round(c.fulfilment_percent)}%
          </span>
        </span>
        <span className="mt-1 block text-xs text-slate-600">
          {c.met} met, {c.partially_met} partly, {c.unmet} not met
        </span>
      </span>
      <span
        className={cn(
          'col-span-2 justify-self-start rounded-full px-2 py-0.5 text-xs font-medium sm:col-span-1 sm:justify-self-end',
          mustOk ? 'bg-brand-100 text-brand-800' : 'bg-amber-100 text-amber-900',
        )}
      >
        Must-haves {c.must_have_met}/{c.must_have_total}
      </span>
    </button>
  )
}
