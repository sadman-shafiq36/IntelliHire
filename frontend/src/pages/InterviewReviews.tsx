// the reviews written after the analysis, as three views on the analysis page: the cv's
// claims checked against the interview, the interview performance score, and the quality
// of the interview itself. each view can be written again on its own

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'

import { api, ApiError } from '@/api/client'
import type {
  CandidateQuote,
  ClaimFlag,
  ClaimStatus,
  InterviewReviews,
  InterviewScoreDimension,
  QualityQuestion,
  QualityResult,
  Review,
  ReviewKind,
} from '@/api/types'
import { Alert, Badge, Button, Card, EmptyState, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { elapsed } from '@/pages/TranscriptLines'

type Turns = Map<number, InterviewReviews['turns'][number]>

export function useInterviewReviews(interviewId: string) {
  return useQuery({
    queryKey: ['reviews', interviewId],
    queryFn: () => api.get<InterviewReviews>(`/api/v1/interviews/${interviewId}/reviews`),
    // they are written by themselves after the analysis; watch them arrive
    refetchInterval: (q) => {
      const d = q.state.data
      const busy = [d?.claims, d?.interview_score, d?.quality].some((r) => r?.status === 'waiting' || r?.status === 'running')
      return busy ? 4000 : false
    },
  })
}

const WORKING: Record<ReviewKind, string> = {
  claims: 'Checking the CV’s claims against the interview.',
  interview_score: 'Scoring the interview.',
  quality: 'Reviewing how the interview was run.',
}

function Frame<T>({
  interviewId,
  kind,
  review,
  label,
  children,
}: {
  interviewId: string
  kind: ReviewKind
  review: Review<T> | null
  label: string
  children: (result: T) => ReactNode
}) {
  const queryClient = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useBackgroundJob(jobId)
  const again = useMutation({
    mutationFn: () => api.post<{ background_job_id: string }>(`/api/v1/interviews/${interviewId}/reviews/${kind}`),
    onSuccess: (r) => {
      setJobId(r.background_job_id)
      void queryClient.invalidateQueries({ queryKey: ['reviews', interviewId] })
    },
  })
  useEffect(() => {
    if (isDone(job.data)) void queryClient.invalidateQueries({ queryKey: ['reviews', interviewId] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.data?.state])

  const working = review?.status === 'waiting' || review?.status === 'running' || (jobId !== null && !isDone(job.data))
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          {review?.generated_at && review.status === 'done' && `Written by ${review.model_name ?? 'code only'}.`}
        </p>
        <Button variant="outline" onClick={() => again.mutate()} disabled={working || again.isPending}>
          <RefreshCw aria-hidden="true" className="h-4 w-4" />
          {review ? `${label} again` : label}
        </Button>
      </div>
      {working && (
        <Alert tone="info">
          <Spinner label={job.data?.message || WORKING[kind]} />
        </Alert>
      )}
      {again.error && <Alert tone="error">{again.error instanceof ApiError ? again.error.message : 'Could not start.'}</Alert>}
      {!working && review?.status === 'failed' && <Alert tone="error">{review.message || 'This review failed.'}</Alert>}
      {!working && review?.status === 'empty' && <Alert tone="warning">{review.message || 'There was nothing to review.'}</Alert>}
      {review?.stale && !working && <Alert tone="warning">The interview was analysed again after this was written. Run it again to match.</Alert>}
      {review?.warnings.map((w) => (
        <Alert key={w} tone="warning">
          {w}
        </Alert>
      ))}
      {!review && !working && (
        <EmptyState title="Not written yet">This is written by itself after the analysis. Press {label} to write it now.</EmptyState>
      )}
      {review?.status === 'done' && review.result && children(review.result)}
    </div>
  )
}

function Quotes({ quotes, turns }: { quotes: CandidateQuote[]; turns: Turns }) {
  if (!quotes.length) return null
  return (
    <ul className="mt-2 space-y-1">
      {quotes.map((q) => {
        const t = turns.get(q.turn_id)
        return (
          <li key={`${q.turn_id}-${q.quote}`} className="border-l-2 border-marker pl-3 text-sm text-slate-800">
            <span className="text-xs text-slate-500">Candidate{t ? ` at ${elapsed(t.start_offset_ms)}` : ''}: </span>
            <mark className="rounded-sm bg-marker px-0.5">{q.quote}</mark>
          </li>
        )
      })}
    </ul>
  )
}

function Meter({ value }: { value: number | null }) {
  return (
    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
      <div className="h-full rounded-full bg-brand-600" style={{ width: `${value ?? 0}%` }} />
    </div>
  )
}

// --- cv claims -------------------------------------------------------------------------

const CLAIM: Record<ClaimStatus, { label: string; tone: 'success' | 'warning' | 'neutral' }> = {
  contradiction: { label: 'Answer differs from the CV', tone: 'warning' },
  unverified: { label: 'Not substantiated', tone: 'warning' },
  not_discussed: { label: 'Never discussed', tone: 'neutral' },
  not_checked: { label: 'Not checked', tone: 'neutral' },
  supported: { label: 'Supported', tone: 'success' },
}
const CLAIM_ORDER: ClaimStatus[] = ['contradiction', 'unverified', 'not_discussed', 'not_checked', 'supported']

export function ClaimsView({ interviewId, d }: { interviewId: string; d: InterviewReviews }) {
  const turns: Turns = new Map(d.turns.map((t) => [t.id, t]))
  return (
    <Frame interviewId={interviewId} kind="claims" review={d.claims} label="Check claims">
      {(r) => {
        const claims = [...r.claims].sort((a, b) => CLAIM_ORDER.indexOf(a.status) - CLAIM_ORDER.indexOf(b.status))
        return (
          <>
            <p className="text-sm text-slate-600">
              Claims from the CV, compared with what the candidate said. These are prompts to follow up, not conclusions:
              people describe their work differently when asked, and a transcript can mishear.
            </p>
            <ul className="flex flex-wrap gap-2">
              {CLAIM_ORDER.map((s) => {
                const n = r.claims.filter((c) => c.status === s).length
                return n ? (
                  <li key={s}>
                    <Badge tone={CLAIM[s].tone}>
                      {CLAIM[s].label}: {n}
                    </Badge>
                  </li>
                ) : null
              })}
            </ul>
            <ol className="space-y-3">
              {claims.map((c) => (
                <ClaimCard key={`${c.topic}-${c.cv_quote}`} c={c} turns={turns} />
              ))}
            </ol>
          </>
        )
      }}
    </Frame>
  )
}

function ClaimCard({ c, turns }: { c: ClaimFlag; turns: Turns }) {
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-slate-500">{c.topic}</p>
          <p className="font-medium text-slate-900">{c.claim}</p>
        </div>
        <Badge tone={CLAIM[c.status].tone}>{CLAIM[c.status].label}</Badge>
      </div>
      <p className="mt-2 border-l-2 border-marker pl-3 text-sm text-slate-800">
        <span className="text-xs text-slate-500">CV: </span>
        <mark className="rounded-sm bg-marker px-0.5">{c.cv_quote}</mark>
      </p>
      <Quotes quotes={c.evidence} turns={turns} />
      {c.explanation && <p className="mt-2 text-sm text-slate-700">{c.explanation}</p>}
      {c.follow_up && (
        <p className="mt-2 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
          <span className="text-xs font-medium text-slate-500">Ask: </span>
          {c.follow_up}
        </p>
      )}
    </li>
  )
}

// --- interview score -------------------------------------------------------------------

export function InterviewScoreView({ interviewId, d }: { interviewId: string; d: InterviewReviews }) {
  const turns: Turns = new Map(d.turns.map((t) => [t.id, t]))
  return (
    <Frame interviewId={interviewId} kind="interview_score" review={d.interview_score} label="Score">
      {(r) => (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <p className="text-sm text-slate-600">Interview performance</p>
              <p className="tabular mt-1 text-3xl font-semibold text-slate-900">{r.overall ?? '–'}</p>
              <p className="mt-1 text-xs text-slate-500">
                From the interview only, over {r.dimensions.filter((x) => x.score !== null).length} of {r.dimensions.length} dimensions
              </p>
            </Card>
            <Card>
              <p className="text-sm text-slate-600">CV-based score, for comparison</p>
              <p className="tabular mt-1 text-3xl font-semibold text-slate-900">{d.cv_score ?? '–'}</p>
              <p className="mt-1 text-xs text-slate-500">From the Scoring step, before the interview</p>
            </Card>
          </div>
          <ol className="space-y-3">
            {r.dimensions.map((x) => (
              <DimensionCard key={x.name} x={x} turns={turns} />
            ))}
          </ol>
        </>
      )}
    </Frame>
  )
}

function DimensionCard({ x, turns }: { x: InterviewScoreDimension; turns: Turns }) {
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-slate-900">{x.label}</p>
          <p className="text-xs text-slate-500">
            Weight {x.effective_weight}% · {x.basis}
            {x.base !== null && x.adjustment !== 0 && ` · measured ${x.base}, AI ${x.adjustment > 0 ? '+' : ''}${x.adjustment}`}
          </p>
        </div>
        <p className="tabular text-2xl font-semibold text-slate-900">{x.score ?? '–'}</p>
      </div>
      <Meter value={x.score} />
      {x.score === null && <p className="mt-2 text-sm text-slate-500">The interview gave no evidence on this.</p>}
      {x.reason && <p className="mt-2 text-sm text-slate-700">{x.reason}</p>}
      <Quotes quotes={x.evidence} turns={turns} />
    </li>
  )
}

// --- interview quality -----------------------------------------------------------------

const SIGNAL = { high: { label: 'High signal', tone: 'success' }, medium: { label: 'Some signal', tone: 'neutral' }, low: { label: 'Little signal', tone: 'warning' } } as const
const STYLE: Record<NonNullable<QualityQuestion['style']>, string> = {
  open: 'open',
  closed: 'closed',
  leading: 'leading',
  multi_part: 'several questions in one',
  unclear: 'unclear',
}

export function QualityView({ interviewId, d }: { interviewId: string; d: InterviewReviews }) {
  return (
    <Frame interviewId={interviewId} kind="quality" review={d.quality} label="Review">
      {(r) => <Quality r={r} />}
    </Frame>
  )
}

function Quality({ r }: { r: QualityResult }) {
  const m = r.metrics
  const total = m.speaking_ms.interviewer + m.speaking_ms.candidate
  const parts: [keyof QualityResult['scores'], string, string][] = [
    ['question_effectiveness', 'Question effectiveness', 'How much evidence each question drew out'],
    ['coverage', 'Requirement coverage', `${m.must_haves_explored} of ${m.must_haves} must-haves explored`],
    ['balance', 'Speaking balance', `Candidate spoke ${m.candidate_share ?? '–'}% of the time`],
    ['consistency', 'Consistency', m.peers ? `Against ${m.peers} other interview(s) for this job` : 'No other interviews for this job yet'],
  ]
  return (
    <>
      <p className="text-sm text-slate-600">How the interview was run, to help the next one. This reviews the questions and the process, not the candidate.</p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card>
          <p className="text-sm text-slate-600">Interview quality</p>
          <p className="tabular mt-1 text-3xl font-semibold text-slate-900">{r.scores.overall ?? '–'}</p>
        </Card>
        {parts.map(([k, label, note]) => (
          <Card key={k}>
            <p className="text-sm text-slate-600">{label}</p>
            <p className="tabular mt-1 text-2xl font-semibold text-slate-900">{r.scores[k] ?? '–'}</p>
            <Meter value={r.scores[k]} />
            <p className="mt-1 text-xs text-slate-500">{note}</p>
          </Card>
        ))}
      </div>

      {total > 0 && (
        <Card title="Speaking time">
          <div className="flex h-3 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
            <div className="h-full bg-slate-500" style={{ width: `${(100 * m.speaking_ms.interviewer) / total}%` }} />
            <div className="h-full bg-brand-600" style={{ width: `${(100 * m.speaking_ms.candidate) / total}%` }} />
          </div>
          <p className="mt-2 text-sm text-slate-700">
            Interviewer {Math.round(m.speaking_ms.interviewer / 1000)} s · candidate {Math.round(m.speaking_ms.candidate / 1000)} s ·{' '}
            {m.questions} questions, {m.follow_ups} follow-ups, {m.overlaps} overlaps · answers averaged {m.average_answer_words} words
          </p>
        </Card>
      )}

      {r.summary && <p className="text-sm text-slate-700">{r.summary}</p>}

      <section aria-labelledby="recommendations-heading" className="space-y-3">
        <h3 id="recommendations-heading" className="text-base font-semibold text-slate-900">
          Recommendations
        </h3>
        {r.recommendations.length === 0 && <p className="text-sm text-slate-600">Nothing stood out to improve.</p>}
        <ul className="space-y-3">
          {r.recommendations.map((x) => (
            <li key={x.issue} className="rounded-xl border border-slate-200 bg-white p-4">
              <p className="font-medium text-slate-900">{x.title}</p>
              <p className="mt-1 text-xs text-slate-500">{x.evidence}</p>
              <p className="mt-2 text-sm text-slate-700">{x.text}</p>
              {x.written_by === 'rule' && <p className="mt-1 text-xs text-slate-500">Standard advice for this finding.</p>}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="questions-heading" className="space-y-3">
        <h3 id="questions-heading" className="text-base font-semibold text-slate-900">
          Questions
        </h3>
        <ol className="space-y-3">
          {r.questions.map((q) => (
            <li key={q.sequence} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="min-w-0 flex-1 text-sm font-medium text-slate-900">
                  <span className="text-xs font-normal text-slate-500">Question {q.sequence}: </span>
                  {q.question}
                </p>
                {q.signal && <Badge tone={SIGNAL[q.signal].tone}>{SIGNAL[q.signal].label}</Badge>}
              </div>
              {q.style && <p className="mt-1 text-xs text-slate-500">Asked as {STYLE[q.style]}</p>}
              {q.reason && <p className="mt-2 text-sm text-slate-700">{q.reason.replace(/\s*\[T\d+\]/g, '')}</p>}
              {q.better_question && (
                <p className="mt-2 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  <span className="text-xs font-medium text-slate-500">Try instead: </span>
                  {q.better_question}
                </p>
              )}
            </li>
          ))}
        </ol>
      </section>
    </>
  )
}
