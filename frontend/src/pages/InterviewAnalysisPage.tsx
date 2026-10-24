// one interview's q&a analysis: each question with the answer, four scores and a
// justification whose transcript citations open the line they point at, and the job's
// requirements as covered, partly covered or never explored, with follow-up questions.
// the reviews written after it (cv claims, interview score, quality) are the last tabs

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ClipboardCheck, CornerDownRight, FileText, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { CoverageStatus, InterviewAnalysis, QaPair, QaScoreName } from '@/api/types'
import { Alert, Badge, Button, buttonClass, Card, EmptyState, PageHeader, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'
import { formatWhen } from '@/pages/InterviewsPanel'
import { ClaimsView, InterviewScoreView, QualityView, useInterviewReviews } from '@/pages/InterviewReviews'
import { elapsed } from '@/pages/TranscriptLines'

const SCORES: [QaScoreName, string][] = [
  ['relevance', 'Relevance'],
  ['accuracy', 'Accuracy'],
  ['completeness', 'Completeness'],
  ['articulation', 'Articulation'],
]

const BASIS: Record<NonNullable<QaPair['accuracy_basis']>, string> = {
  reference: 'checked against your reference material',
  general: 'judged on general knowledge',
  not_applicable: 'nothing to check',
}

const COVERAGE: Record<CoverageStatus, { label: string; tone: 'success' | 'warning' | 'error' }> = {
  covered: { label: 'Covered', tone: 'success' },
  partially_covered: { label: 'Partly covered', tone: 'warning' },
  not_explored: { label: 'Never explored', tone: 'error' },
}

type View = 'answers' | 'coverage' | 'claims' | 'score' | 'quality'
const VIEWS: View[] = ['answers', 'coverage', 'claims', 'score', 'quality']
type Turns = Map<number, InterviewAnalysis['turns'][number]>

export function InterviewAnalysisPage() {
  const { interviewId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const asked = params.get('view') as View | null
  const view: View = asked && VIEWS.includes(asked) ? asked : 'answers'
  const queryClient = useQueryClient()
  const key = ['analysis', interviewId]
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useBackgroundJob(jobId)
  const reviews = useInterviewReviews(interviewId)

  const data = useQuery({
    queryKey: key,
    queryFn: () => api.get<InterviewAnalysis>(`/api/v1/interviews/${interviewId}/analysis`),
    // the analysis starts by itself when the interview ends; watch it arrive
    refetchInterval: (q) => {
      const s = q.state.data?.analysis?.status
      return s === 'waiting' || s === 'running' ? 4000 : false
    },
  })

  const again = useMutation({
    mutationFn: () => api.post<{ background_job_id: string }>(`/api/v1/interviews/${interviewId}/analysis`),
    onSuccess: (r) => {
      setJobId(r.background_job_id)
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })

  useEffect(() => {
    if (isDone(job.data)) {
      void queryClient.invalidateQueries({ queryKey: key })
      // a new analysis queues new reviews
      void queryClient.invalidateQueries({ queryKey: ['reviews', interviewId] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.data?.state])

  // a question picked in the coverage view is scrolled to once the answers are showing
  const [target, setTarget] = useState<number | null>(null)
  useEffect(() => {
    if (target !== null && view === 'answers') {
      document.getElementById(`pair-${target}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setTarget(null)
    }
  }, [target, view])

  if (data.isLoading) return <Spinner label="Loading the analysis" />
  if (data.error || !data.data)
    return <Alert tone="error">{data.error instanceof ApiError ? data.error.message : 'Could not load the analysis.'}</Alert>

  const d = data.data
  const a = d.analysis
  const working = a?.status === 'waiting' || a?.status === 'running' || (jobId !== null && !isDone(job.data))
  const turns: Turns = new Map(d.turns.map((t) => [t.id, t]))

  return (
    <div className="space-y-6">
      <PageHeader
        back={
          <Link to={`/jobs/${d.interview.job_id}?tab=interviews`} className="inline-flex items-center gap-1 text-slate-600 hover:text-slate-900">
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            {d.interview.job_title}
          </Link>
        }
        title={`Interview analysis: ${d.interview.candidate_name}`}
        description={
          d.interview.ended_at
            ? `Interview ended ${formatWhen(d.interview.ended_at, d.interview.timezone_name)}. Every judgement cites the transcript lines it rests on.`
            : 'The analysis runs once the interview has ended.'
        }
        actions={
          <>
            <Link to={`/interviews/${d.interview.id}/transcript`} className={buttonClass({ variant: 'outline' })}>
              <FileText aria-hidden="true" className="h-4 w-4" />
              Transcript
            </Link>
            {a?.status === 'analysed' && (
              <Link to={`/interviews/${d.interview.id}/assessment`} className={buttonClass({})}>
                <ClipboardCheck aria-hidden="true" className="h-4 w-4" />
                Consolidated report
              </Link>
            )}
            {d.interview.status === 'completed' && (
              <Button variant="outline" onClick={() => again.mutate()} disabled={working || again.isPending}>
                <RefreshCw aria-hidden="true" className="h-4 w-4" />
                {a ? 'Analyse again' : 'Analyse'}
              </Button>
            )}
          </>
        }
      />

      {working && (
        <Alert tone="info">
          <Spinner label={job.data?.message || (a?.status === 'waiting' ? 'Waiting to start.' : 'Analysing the interview. This takes about a minute.')} />
        </Alert>
      )}
      {again.error && <Alert tone="error">{again.error instanceof ApiError ? again.error.message : 'Could not start.'}</Alert>}
      {!working && a?.status === 'failed' && <Alert tone="error">{a.message || 'The analysis failed.'} Press Analyse again.</Alert>}
      {!working && a?.status === 'empty' && <Alert tone="warning">{a.message || 'There was nothing to analyse.'}</Alert>}
      {a?.stale && !working && (
        <Alert tone="warning">The transcript has lines the analysis didn't see. Analyse again to include them.</Alert>
      )}
      {a?.warnings.map((w) => (
        <Alert key={w} tone="warning">
          {w}
        </Alert>
      ))}

      {d.pairs.length === 0 && !working && a?.status !== 'empty' && (
        <EmptyState title="No analysis yet">
          {d.interview.status === 'completed'
            ? 'Press Analyse to split the transcript into questions and answers and evaluate them.'
            : 'The analysis runs by itself when the interview ends.'}
        </EmptyState>
      )}

      {d.pairs.length > 0 && (
        <>
          <Summary d={d} />
          <div role="tablist" aria-label="Analysis view" className="-mx-4 flex gap-1 overflow-x-auto border-b border-slate-200 px-4 sm:mx-0 sm:px-0">
            {(
              [
                ['answers', `Questions and answers (${d.pairs.length})`],
                ['coverage', `Requirement coverage (${d.coverage.length})`],
                ['claims', 'CV claims'],
                ['score', 'Interview score'],
                ['quality', 'Interview quality'],
              ] as [View, string][]
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={view === id}
                onClick={() => setParams(id === 'answers' ? {} : { view: id }, { replace: true })}
                className={cn(
                  '-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium',
                  view === id ? 'border-brand-600 text-slate-900' : 'border-transparent text-slate-600 hover:text-slate-900',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          {view === 'answers' && (
            <ol className="space-y-4">
              {d.pairs.map((p) => (
                <PairCard key={p.id} p={p} turns={turns} />
              ))}
            </ol>
          )}
          {view === 'coverage' && (
            <CoverageView
              d={d}
              onOpen={(sequence) => {
                setTarget(sequence)
                setParams({}, { replace: true })
              }}
            />
          )}
          {(view === 'claims' || view === 'score' || view === 'quality') &&
            (reviews.isLoading ? (
              <Spinner label="Loading" />
            ) : reviews.error || !reviews.data ? (
              <Alert tone="error">{reviews.error instanceof ApiError ? reviews.error.message : 'Could not load the reviews.'}</Alert>
            ) : view === 'claims' ? (
              <ClaimsView interviewId={interviewId} d={reviews.data} />
            ) : view === 'score' ? (
              <InterviewScoreView interviewId={interviewId} d={reviews.data} />
            ) : (
              <QualityView interviewId={interviewId} d={reviews.data} />
            ))}
          {a?.model_name && (
            <p className="text-xs text-slate-500">
              Analysed by {a.model_name}
              {a.segmented_by === 'exchanges' && '; questions split by who spoke, as the AI split could not be used'}.
            </p>
          )}
        </>
      )}
    </div>
  )
}

function Summary({ d }: { d: InterviewAnalysis }) {
  const s = d.summary
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <Card>
        <p className="text-sm text-slate-600">Answer quality</p>
        <p className="tabular mt-1 text-3xl font-semibold text-slate-900">{s.answer_quality ?? '–'}</p>
        <p className="mt-1 text-xs text-slate-500">Average over {s.pairs} answers</p>
      </Card>
      <Card>
        <dl className="space-y-2">
          {SCORES.map(([k, label]) => (
            <Meter key={k} label={label} value={s.averages[k]} />
          ))}
        </dl>
      </Card>
      <Card>
        <p className="text-sm text-slate-600">Requirements</p>
        <ul className="mt-2 space-y-1 text-sm">
          <li>
            <span className="tabular font-semibold text-slate-900">{s.covered}</span> covered
          </li>
          <li>
            <span className="tabular font-semibold text-slate-900">{s.partially_covered}</span> partly covered
          </li>
          <li>
            <span className="tabular font-semibold text-slate-900">{s.not_explored}</span> never explored
          </li>
        </ul>
      </Card>
    </div>
  )
}

function Meter({ label, value }: { label: string; value: number | null }) {
  return (
    <div title={`${label}: ${value ?? 'not judged'}`}>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <dt className="text-slate-600">{label}</dt>
        <dd className="tabular font-medium text-slate-900">{value === null ? '–' : Math.round(value)}</dd>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
        <div className="h-full rounded-full bg-brand-600" style={{ width: `${value ?? 0}%` }} />
      </div>
    </div>
  )
}

// the justification with each [T12] as a button that shows the line, and [S1] named
function Justification({ text, p, turns }: { text: string; p: QaPair; turns: Turns }) {
  const [open, setOpen] = useState<number | null>(null)
  const titles = new Map(p.sources.map((s) => [s.label, s.title]))
  const parts = text.split(/(\[T\d+\]|\[S\d+\])/g)
  const shown = open !== null ? turns.get(open) : undefined
  return (
    <div>
      <p className="text-sm leading-relaxed text-slate-700">
        {parts.map((part, i) => {
          const t = part.match(/^\[T(\d+)\]$/)
          if (t) {
            const id = Number(t[1])
            return (
              <button
                key={i}
                type="button"
                onClick={() => setOpen(open === id ? null : id)}
                aria-expanded={open === id}
                className="mx-0.5 rounded bg-slate-100 px-1 font-mono text-xs text-brand-700 hover:bg-slate-200"
              >
                line {turns.has(id) ? elapsed(turns.get(id)!.start_offset_ms) : id}
              </button>
            )
          }
          const s = part.match(/^\[(S\d+)\]$/)
          if (s) return <span key={i} className="mx-0.5 text-xs text-slate-500">({titles.get(s[1]) ?? s[1]})</span>
          return <span key={i}>{part}</span>
        })}
      </p>
      {shown && (
        <blockquote className="mt-2 border-l-2 border-marker pl-3 text-sm text-slate-800">
          <span className="text-xs text-slate-500">
            {shown.speaker_role === 'candidate' ? 'Candidate' : 'Interviewer'} at {elapsed(shown.start_offset_ms)}:{' '}
          </span>
          <mark className="rounded-sm bg-marker px-0.5">{shown.text}</mark>
        </blockquote>
      )}
    </div>
  )
}

function PairCard({ p, turns }: { p: QaPair; turns: Turns }) {
  const asked = p.question_turn_ids.length ? turns.get(p.question_turn_ids[0]) : undefined
  return (
    <li id={`pair-${p.sequence}`} className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-slate-500">
            Question {p.sequence}
            {asked && ` · ${elapsed(asked.start_offset_ms)} into the interview`}
          </p>
          {p.follow_up_to !== null && (
            <a href={`#pair-${p.follow_up_to}`} className="mt-1 inline-flex items-center gap-1 text-xs text-brand-700 hover:underline">
              <CornerDownRight aria-hidden="true" className="h-3.5 w-3.5" />
              Follows up question {p.follow_up_to}
            </a>
          )}
          <p className="mt-1 font-medium text-slate-900">{p.question}</p>
        </div>
        <div className="text-right">
          <p className="tabular text-2xl font-semibold text-slate-900">{p.score ?? '–'}</p>
          <p className="text-xs text-slate-500">answer score</p>
        </div>
      </div>

      <div className="mt-3 rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700">
        {p.answer || <span className="text-slate-500">No answer was given.</span>}
      </div>

      {p.score !== null && (
        <dl className="mt-4 grid gap-3 sm:grid-cols-4">
          {SCORES.map(([k, label]) => (
            <Meter key={k} label={label} value={p.scores[k]} />
          ))}
        </dl>
      )}
      {p.accuracy_basis && p.score !== null && (
        <p className="mt-1 text-xs text-slate-500">Accuracy {BASIS[p.accuracy_basis]}.</p>
      )}

      {p.justification && (
        <div className="mt-3">
          <Justification text={p.justification} p={p} turns={turns} />
        </div>
      )}

      {p.requirements.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {p.requirements.map((r) => (
            <li
              key={r.requirement_id}
              title={r.note ?? undefined}
              // requirement names are long; unlike Badge this wraps on a phone
              className={cn(
                'rounded-2xl px-2.5 py-0.5 text-xs font-medium',
                r.coverage === 'covered' ? 'bg-brand-100 text-brand-800' : 'bg-amber-100 text-amber-900',
              )}
            >
              {r.text} · {COVERAGE[r.coverage].label.toLowerCase()}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

function CoverageView({ d, onOpen }: { d: InterviewAnalysis; onOpen: (sequence: number) => void }) {
  const followUps = new Map<number, string[]>()
  for (const f of d.follow_ups) followUps.set(f.requirement_id, [...(followUps.get(f.requirement_id) ?? []), f.question])
  return (
    <ul className="space-y-3">
      {d.coverage.map((c) => (
        <li key={c.requirement_id} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="min-w-0 flex-1 font-medium text-slate-900">
              {c.text}
              {c.must_have && <span className="ml-2 text-xs font-normal text-slate-500">must-have</span>}
            </p>
            <Badge tone={COVERAGE[c.status].tone}>{COVERAGE[c.status].label}</Badge>
          </div>
          {c.pairs.length > 0 && (
            <ul className="mt-2 space-y-1 text-sm text-slate-700">
              {c.pairs.map((p) => (
                <li key={p.sequence}>
                  <button type="button" onClick={() => onOpen(p.sequence)} className="text-brand-700 hover:underline">
                    Question {p.sequence}
                  </button>{' '}
                  · {COVERAGE[p.coverage].label.toLowerCase()}
                  {p.note && <span className="text-slate-500"> · {p.note}</span>}
                </li>
              ))}
            </ul>
          )}
          {(followUps.get(c.requirement_id) ?? []).map((q) => (
            <p key={q} className="mt-2 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
              <span className="text-xs font-medium text-slate-500">Ask next time: </span>
              {q}
            </p>
          ))}
        </li>
      ))}
    </ul>
  )
}
