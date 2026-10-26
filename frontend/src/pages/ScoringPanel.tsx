// scoring step: score the ranked candidates, tune the weights, shortlist and pick who to interview

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Calculator, CalendarCheck, RefreshCw, Sparkles, Star, UserX } from 'lucide-react'
import { useEffect, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type {
  ScoreDimensionName,
  ScoredCandidate,
  ScoreProfile,
  ScoringResults,
} from '@/api/types'
import { Alert, Button, Card, EmptyState, ProgressBar, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'
import { DIMENSION_HELP, DIMENSION_LABEL, DIMENSION_SHORT, DIMENSIONS } from '@/lib/scoring'
import { ScreeningFeedbackQueue, useScreeningFeedback } from '@/pages/FeedbackPanels'
import { ScoreBreakdownDrawer } from '@/pages/ScoreBreakdown'

type Filter = 'all' | 'shortlist'

export function ScoringPanel({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient()
  const [runId, setRunId] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const progress = useBackgroundJob(runId)

  const results = useQuery({
    queryKey: ['scoring', jobId],
    queryFn: () => api.get<ScoringResults>(`/api/v1/jobs/${jobId}/scoring`),
  })

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['scoring', jobId] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
    void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }

  useEffect(() => {
    if (progress.data) refresh()
    // refresh only depends on the job id and the query client
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.completed, progress.data?.state])

  const start = useMutation({
    mutationFn: (rescore: boolean) =>
      api.post<{ background_job_id: string; candidate_count: number }>(
        `/api/v1/jobs/${jobId}/scoring`,
        { rescore },
      ),
    onSuccess: (data) => setRunId(data.background_job_id),
  })

  const decide = useMutation({
    mutationFn: ({ id, ...body }: { id: number; shortlisted?: boolean; selected_for_interview?: boolean }) =>
      api.patch(`/api/v1/jobs/${jobId}/scoring/${id}`, body),
    onSuccess: refresh,
  })

  // screening feedback goes only to people marked here, never to anyone by rank
  const feedback = useScreeningFeedback(jobId)
  const marked = new Map((feedback.data ?? []).map((r) => [r.candidate_id, r]))
  const markNot = useMutation({
    mutationFn: ({ id, mark }: { id: number; mark: boolean }) =>
      mark
        ? api.post(`/api/v1/jobs/${jobId}/feedback/not-selected`, { candidate_ids: [id] })
        : api.delete(`/api/v1/jobs/${jobId}/feedback/not-selected/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['feedback-queue', jobId] }),
  })

  const running = runId !== null && !isDone(progress.data)
  const data = results.data
  const waiting = data?.waiting_count ?? 0
  const scoredAny = !!data && data.ranked.length > 0
  const shortlist = data?.ranked.filter((c) => c.is_shortlisted) ?? []
  const shown = filter === 'shortlist' ? shortlist : (data?.ranked ?? [])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="max-w-2xl text-sm text-slate-600">
          Each candidate in the ranking gets a score from 0 to 100 for each dimension. It starts
          from their checked screening evidence, and the AI may move it a little, but only with a
          quote from the CV. The weights below combine the dimensions into one score.
        </p>
        <div className="flex gap-2">
          {scoredAny && (
            <Button
              variant="outline"
              disabled={running || start.isPending}
              onClick={() => {
                if (window.confirm('Score every candidate again? Shortlist and selections are kept.'))
                  start.mutate(true)
              }}
            >
              <RefreshCw aria-hidden="true" className="h-4 w-4" />
              Score all again
            </Button>
          )}
          <Button disabled={running || start.isPending || waiting === 0} onClick={() => start.mutate(false)}>
            <Calculator aria-hidden="true" className="h-4 w-4" />
            {waiting > 0 ? `Score ${waiting} candidate${waiting === 1 ? '' : 's'}` : 'All scored'}
          </Button>
        </div>
      </div>

      {start.error && (
        <Alert tone="error">
          {start.error instanceof ApiError ? start.error.message : 'Scoring could not start.'}
        </Alert>
      )}
      {markNot.error && (
        <Alert tone="error">
          {markNot.error instanceof ApiError ? markNot.error.message : 'That change was not saved.'}
        </Alert>
      )}
      {decide.error && (
        <Alert tone="error">
          {decide.error instanceof ApiError ? decide.error.message : 'That change was not saved.'}
        </Alert>
      )}

      {running && progress.data && (
        <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-5" aria-live="polite">
          <div className="flex items-center justify-between gap-3 text-sm">
            <Spinner label={progress.data.message || 'Scoring'} />
            {progress.data.total > 0 && (
              <span className="tabular text-slate-600">
                {progress.data.completed} of {progress.data.total}
              </span>
            )}
          </div>
          <ProgressBar percent={progress.data.percent} />
          <p className="text-xs text-slate-500">
            A few seconds per candidate. You can leave this page; it carries on.
          </p>
        </div>
      )}
      {progress.data?.state === 'failed' && <Alert tone="error">{progress.data.message}</Alert>}

      {results.isLoading && <Spinner label="Loading scores" />}

      {data && !scoredAny && !running && (
        <EmptyState
          icon={<Calculator className="h-8 w-8" aria-hidden="true" />}
          title={waiting > 0 ? 'Ready to score' : 'Nobody to score yet'}
        >
          {waiting > 0
            ? `${waiting} candidate${waiting === 1 ? ' is' : 's are'} in the screening ranking. Scoring sets the weights for this role and scores each one.`
            : 'Screen CVs first. Only candidates in the screening ranking are scored; anyone ruled out by a must-have is not.'}
        </EmptyState>
      )}

      {data && (scoredAny || data.profile) && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section aria-labelledby="scores-heading" className="min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="scores-heading" className="text-lg font-semibold text-slate-900">
                Score ranking
              </h2>
              <div role="tablist" aria-label="Show" className="flex rounded-lg border border-slate-200 bg-white p-0.5 text-sm">
                {(
                  [
                    ['all', `All (${data.ranked.length})`],
                    ['shortlist', `Shortlist (${shortlist.length})`],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={filter === id}
                    onClick={() => setFilter(id)}
                    className={cn(
                      'rounded-md px-3 py-1 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                      filter === id ? 'bg-slate-900 text-white' : 'text-slate-600 hover:text-slate-900',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {shown.length === 0 ? (
              <p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-5 py-6 text-sm text-slate-600">
                {filter === 'shortlist'
                  ? 'Nobody is shortlisted yet. Use the star on a candidate to add them.'
                  : 'No scores yet.'}
              </p>
            ) : (
              <ol className="mt-3 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
                {shown.map((c) => (
                  <li key={c.candidate_id} className="flex items-stretch">
                    <ScoreRow candidate={c} weights={data.profile?.weights} onOpen={() => setOpen(c.candidate_id)} />
                    <div className="flex w-14 shrink-0 flex-col border-l border-slate-100">
                    <button
                      type="button"
                      aria-pressed={c.is_shortlisted}
                      aria-label={c.is_shortlisted ? `Remove ${c.full_name} from the shortlist` : `Shortlist ${c.full_name}`}
                      title={c.is_shortlisted ? 'On the shortlist' : 'Add to the shortlist'}
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ id: c.candidate_id, shortlisted: !c.is_shortlisted })}
                      className="flex flex-1 items-center justify-center text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
                    >
                      <Star
                        aria-hidden="true"
                        className={cn('h-5 w-5', c.is_shortlisted && 'fill-brand-600 text-brand-600')}
                      />
                    </button>
                    <button
                      type="button"
                      aria-pressed={marked.has(c.candidate_id)}
                      aria-label={
                        marked.has(c.candidate_id)
                          ? `Take back: ${c.full_name} not selected for interview`
                          : `Mark ${c.full_name} as not selected for interview`
                      }
                      title={
                        c.selected_at
                          ? 'Selected for interview'
                          : marked.has(c.candidate_id)
                            ? 'Marked not selected; feedback is in the review queue'
                            : 'Mark as not selected for interview'
                      }
                      disabled={markNot.isPending || !!c.selected_at}
                      onClick={() => markNot.mutate({ id: c.candidate_id, mark: !marked.has(c.candidate_id) })}
                      className="flex flex-1 items-center justify-center border-t border-slate-100 text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <UserX aria-hidden="true" className={cn('h-5 w-5', marked.has(c.candidate_id) && 'text-red-700')} />
                    </button>
                    </div>
                  </li>
                ))}
              </ol>
            )}
            {data.excluded_count > 0 && (
              <p className="mt-2 text-xs text-slate-500">
                {data.excluded_count} candidate{data.excluded_count === 1 ? ' was' : 's were'} ruled out by a
                must-have in screening and {data.excluded_count === 1 ? 'is' : 'are'} not scored.
              </p>
            )}
          </section>

          {data.profile && <WeightsCard jobId={jobId} profile={data.profile} onSaved={refresh} />}
        </div>
      )}

      {data && scoredAny && <ScreeningFeedbackQueue jobId={jobId} />}

      {open !== null && (
        <ScoreBreakdownDrawer
          jobId={jobId}
          candidateId={open}
          onClose={() => setOpen(null)}
          onChanged={refresh}
        />
      )}
    </div>
  )
}

function ScoreRow({
  candidate: c,
  weights,
  onOpen,
}: {
  candidate: ScoredCandidate
  weights: ScoreProfile['weights'] | undefined
  onOpen: () => void
}) {
  const dims = DIMENSIONS.filter((d) => (weights?.[d] ?? 0) > 0)
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group grid min-w-0 flex-1 grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 px-5 py-4 text-left transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none xl:grid-cols-[2.5rem_minmax(0,1fr)_minmax(0,240px)_auto]"
    >
      <span className="tabular text-2xl font-semibold tracking-[-0.02em] text-slate-900">{c.rank}</span>
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="min-w-0 truncate font-medium text-slate-900 group-hover:underline">{c.full_name}</span>
          {c.selected_at && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-800">
              <CalendarCheck aria-hidden="true" className="h-3.5 w-3.5" />
              Selected
            </span>
          )}
        </span>
        <span className="block truncate text-sm text-slate-600">
          {c.stale
            ? 'Screened again since scoring; score again to update'
            : [c.headline, c.years_experience ? `${c.years_experience} years` : null].filter(Boolean).join(', ')}
        </span>
      </span>
      <span className="col-span-3 grid grid-cols-2 gap-x-4 gap-y-1.5 xl:col-span-1">
        {dims.map((d) => (
          <DimensionMeter key={d} name={d} value={c.dimensions[d]} />
        ))}
      </span>
      <span className="col-start-3 row-start-1 text-right xl:col-start-4">
        <span className="tabular block text-2xl font-semibold tracking-[-0.02em] text-slate-900">
          {Math.round(c.aggregate_score)}
        </span>
        <span className="block text-xs text-slate-500">of 100</span>
      </span>
    </button>
  )
}

function DimensionMeter({ name, value }: { name: ScoreDimensionName; value: number | undefined }) {
  const label = `${DIMENSION_LABEL[name]}: ${value === undefined ? 'not scored' : Math.round(value)}`
  return (
    <span className="block min-w-0" title={label}>
      <span className="flex items-baseline justify-between gap-2 text-xs">
        <span className="truncate text-slate-600">{DIMENSION_SHORT[name]}</span>
        <span className="tabular font-medium text-slate-900">{value === undefined ? '–' : Math.round(value)}</span>
      </span>
      <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
        <span className="block h-full rounded-full bg-brand-600" style={{ width: `${value ?? 0}%` }} />
      </span>
      <span className="sr-only">{label}</span>
    </span>
  )
}

function WeightsCard({
  jobId,
  profile,
  onSaved,
}: {
  jobId: string
  profile: ScoreProfile
  onSaved: () => void
}) {
  const [draft, setDraft] = useState(profile.weights)
  const [suggestRun, setSuggestRun] = useState<string | null>(null)
  const suggestion = useBackgroundJob(suggestRun)

  // a save or a new suggestion changes the stored weights; show them
  useEffect(() => setDraft(profile.weights), [profile.weights])
  useEffect(() => {
    if (isDone(suggestion.data)) onSaved()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestion.data?.state])

  const save = useMutation({
    mutationFn: () => api.put<ScoreProfile>(`/api/v1/jobs/${jobId}/scoring/weights`, { weights: draft }),
    onSuccess: onSaved,
  })
  const ask = useMutation({
    mutationFn: () => api.post<{ background_job_id: string }>(`/api/v1/jobs/${jobId}/scoring/weights/suggest`),
    onSuccess: (data) => setSuggestRun(data.background_job_id),
  })

  const total = DIMENSIONS.reduce((sum, d) => sum + (draft[d] ?? 0), 0)
  const changed = DIMENSIONS.some((d) => (draft[d] ?? 0) !== (profile.weights[d] ?? 0))
  const asking = suggestRun !== null && !isDone(suggestion.data)
  const source =
    profile.source === 'ai' ? 'Suggested by the AI' : profile.source === 'edited' ? 'Set by you' : 'Standard weights'

  return (
    <Card
      title="Weights"
      description={`${source}. Changing them re-ranks at once; the dimension scores stay as they are.`}
      className="h-fit"
    >
      <div className="space-y-5">
        {DIMENSIONS.map((d) => {
          const usable = profile.applicable.includes(d)
          const reason = profile.suggested?.reasons[d]
          const share = total > 0 ? Math.round(((draft[d] ?? 0) / total) * 100) : 0
          return (
            <div key={d}>
              <div className="flex items-baseline justify-between gap-3">
                <label htmlFor={`weight-${d}`} className="text-sm font-medium text-slate-900">
                  {DIMENSION_LABEL[d]}
                </label>
                <span className="tabular text-sm text-slate-700">{usable ? `${share}%` : 'not used'}</span>
              </div>
              <input
                id={`weight-${d}`}
                type="range"
                min={0}
                max={100}
                step={5}
                disabled={!usable}
                value={draft[d] ?? 0}
                onChange={(e) => setDraft({ ...draft, [d]: Number(e.target.value) })}
                className="mt-2 w-full accent-brand-600 disabled:opacity-40"
                aria-describedby={`weight-${d}-help`}
              />
              <p id={`weight-${d}-help`} className="mt-1 text-xs leading-relaxed text-slate-600">
                {usable
                  ? reason
                    ? `AI: ${reason}${profile.source === 'edited' && profile.suggested ? ` (suggested ${profile.suggested.weights[d]}%)` : ''}`
                    : DIMENSION_HELP[d]
                  : 'This job has no requirements of this kind.'}
              </p>
            </div>
          )
        })}

        {profile.keywords.length > 0 && (
          <div>
            <p className="text-xs font-medium text-slate-500">Key terms for the description match</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {profile.keywords.map((k) => (
                <span key={k.term} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                  {k.term}
                </span>
              ))}
            </div>
          </div>
        )}

        {(save.error || ask.error || suggestion.data?.state === 'failed') && (
          <Alert tone="error">
            {save.error instanceof ApiError
              ? save.error.message
              : ask.error instanceof ApiError
                ? ask.error.message
                : (suggestion.data?.message ?? 'That did not work.')}
          </Alert>
        )}

        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={!changed || save.isPending || total === 0} onClick={() => save.mutate()}>
            Save weights
          </Button>
          {changed && (
            <Button size="sm" variant="ghost" onClick={() => setDraft(profile.weights)}>
              Undo
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={asking || ask.isPending}
            onClick={() => ask.mutate()}
          >
            <Sparkles aria-hidden="true" className="h-4 w-4" />
            {asking ? 'Asking' : 'Ask the AI again'}
          </Button>
        </div>
      </div>
    </Card>
  )
}
