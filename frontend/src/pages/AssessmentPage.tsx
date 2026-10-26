// one candidate's consolidated report: the position and how its number is made up, the
// requirements with their cv and interview evidence, and the findings, each with the cv
// line or transcript moment behind it. exports as pdf or a printable page

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, ListChecks, Printer, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { AssessmentEvidence, AssessmentReport, FindingKind, FitPosition } from '@/api/types'
import { Alert, Badge, Button, buttonClass, Card, EmptyState, PageHeader, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'
import { InterviewFeedbackPanel } from '@/pages/FeedbackPanels'
import { formatWhen } from '@/pages/InterviewsPanel'
import { elapsed } from '@/pages/TranscriptLines'

export const POSITION_TONE: Record<FitPosition, 'success' | 'warning' | 'error' | 'neutral'> = {
  strong_fit: 'success',
  possible_fit: 'warning',
  not_a_fit: 'error',
  not_assessed: 'neutral',
}

export const KIND_LABEL: Record<FindingKind, string> = {
  strength: 'Strengths',
  weakness: 'Weaknesses',
  evidence_gap: 'Evidence gaps',
  area_to_verify: 'Areas to verify',
}
const KINDS: FindingKind[] = ['strength', 'weakness', 'evidence_gap', 'area_to_verify']

const CV_LABEL = { met: 'Met', partially_met: 'Partly met', unmet: 'Not met' } as const
const INTERVIEW_LABEL = {
  covered: 'Covered',
  partially_covered: 'Partly covered',
  not_explored: 'Not explored',
} as const

export function capitalise(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

async function openBlob(path: string, download?: string) {
  const blob = await api.download(path)
  const url = URL.createObjectURL(blob)
  if (download) {
    const a = document.createElement('a')
    a.href = url
    a.download = download
    a.click()
  } else {
    window.open(url, '_blank', 'noopener')
  }
  // long enough for the new tab or the download to read it
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function AssessmentPage() {
  const { interviewId = '' } = useParams()
  const queryClient = useQueryClient()
  const key = ['assessment', interviewId]
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useBackgroundJob(jobId)
  const [exportError, setExportError] = useState<string | null>(null)
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'feedback' ? 'feedback' : 'report'

  const data = useQuery({
    queryKey: key,
    queryFn: () => api.get<AssessmentReport>(`/api/v1/interviews/${interviewId}/assessment`),
    // it is written after the analysis; watch it arrive
    refetchInterval: (q) => {
      const s = q.state.data?.assessment?.status
      return s === 'waiting' || s === 'running' ? 4000 : false
    },
  })

  const again = useMutation({
    mutationFn: () => api.post<{ background_job_id: string }>(`/api/v1/interviews/${interviewId}/assessment`),
    onSuccess: (r) => {
      setJobId(r.background_job_id)
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })

  useEffect(() => {
    if (isDone(job.data)) void queryClient.invalidateQueries({ queryKey: key })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.data?.state])

  if (data.isLoading) return <Spinner label="Loading the report" />
  if (data.error || !data.data)
    return <Alert tone="error">{data.error instanceof ApiError ? data.error.message : 'Could not load the report.'}</Alert>

  const d = data.data
  const a = d.assessment
  const working = a?.status === 'waiting' || a?.status === 'running' || (jobId !== null && !isDone(job.data))
  const ready = a?.status === 'assessed'
  const base = `/api/v1/interviews/${d.interview.id}/assessment`

  async function exportAs(kind: 'pdf' | 'html') {
    setExportError(null)
    try {
      await openBlob(`${base}/report.${kind}`, kind === 'pdf' ? `assessment-${d.interview.id}.pdf` : undefined)
    } catch (e) {
      setExportError(e instanceof ApiError ? e.message : 'The report could not be exported.')
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        back={
          <Link to={`/jobs/${d.interview.job_id}?tab=assessment`} className="inline-flex items-center gap-1 text-slate-600 hover:text-slate-900">
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            {d.interview.job_title}
          </Link>
        }
        title={`Assessment: ${d.interview.candidate_name}`}
        description={
          d.interview.started_at
            ? `Interviewed ${formatWhen(d.interview.started_at, d.interview.timezone_name)}. CV screening, scores and the interview in one report.`
            : undefined
        }
        actions={
          <>
            <Link to={`/interviews/${d.interview.id}/analysis`} className={buttonClass({ variant: 'outline' })}>
              <ListChecks aria-hidden="true" className="h-4 w-4" />
              Q&amp;A analysis
            </Link>
            {ready && (
              <>
                <Button variant="outline" onClick={() => void exportAs('html')}>
                  <Printer aria-hidden="true" className="h-4 w-4" />
                  Printable page
                </Button>
                <Button variant="outline" onClick={() => void exportAs('pdf')}>
                  <Download aria-hidden="true" className="h-4 w-4" />
                  PDF
                </Button>
              </>
            )}
            {d.analysis_status === 'analysed' && (
              <Button variant="outline" onClick={() => again.mutate()} disabled={working || again.isPending}>
                <RefreshCw aria-hidden="true" className="h-4 w-4" />
                {a ? 'Write again' : 'Write report'}
              </Button>
            )}
          </>
        }
      />

      <div role="tablist" aria-label="Assessment view" className="-mx-4 flex gap-1 overflow-x-auto border-b border-slate-200 px-4 sm:mx-0 sm:px-0">
        {(
          [
            ['report', 'Report'],
            ['feedback', 'Candidate feedback'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={view === id}
            onClick={() => setParams(id === 'report' ? {} : { view: id }, { replace: true })}
            className={cn(
              '-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium',
              view === id ? 'border-brand-600 text-slate-900' : 'border-transparent text-slate-600 hover:text-slate-900',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {view === 'feedback' ? (
        <InterviewFeedbackPanel interviewId={interviewId} />
      ) : (
        <>
      {working && (
        <Alert tone="info">
          <Spinner label={job.data?.message || 'Writing the report. This takes about half a minute.'} />
        </Alert>
      )}
      {exportError && <Alert tone="error">{exportError}</Alert>}
      {again.error && <Alert tone="error">{again.error instanceof ApiError ? again.error.message : 'Could not start.'}</Alert>}
      {!working && a?.status === 'failed' && <Alert tone="error">{a.message || 'The report could not be written.'}</Alert>}
      {a?.stale && !working && (
        <Alert tone="warning">The interview was analysed again after this report. Write it again to bring it up to date.</Alert>
      )}
      {a?.warnings.map((w) => (
        <Alert key={w} tone="warning">
          {w}
        </Alert>
      ))}

      {!a && !working && (
        <EmptyState title="No report yet">
          {d.analysis_status === 'analysed'
            ? 'Press Write report to combine the screening, the scores and the interview analysis.'
            : 'The report is written automatically once the interview has been analysed.'}
        </EmptyState>
      )}

      {a && a.overall_score !== null && a.position && (
        <>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
            <Card>
              <p className="text-sm text-slate-600">Overall position</p>
              <div className="mt-2 flex flex-wrap items-baseline gap-3">
                <Badge tone={POSITION_TONE[a.position]}>{capitalise(a.position_label)}</Badge>
                <span className="tabular text-4xl font-semibold text-slate-900">{a.overall_score}</span>
                <span className="text-sm text-slate-500">of 100</span>
              </div>
              {a.capped_by.length > 0 && (
                <p className="mt-3 text-sm text-slate-600">
                  Not a strong fit while these must-haves have no evidence: {a.capped_by.join('; ')}.
                </p>
              )}
              {a.summary && <p className="mt-4 text-sm leading-relaxed text-slate-700">{a.summary}</p>}
              <p className="mt-4 text-xs text-slate-500">
                The position is worked out from the scores; the decision is yours.
              </p>
            </Card>
            <Card title="How the overall score is made up">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="pb-2 font-medium">Component</th>
                    <th className="pb-2 font-medium">Score</th>
                    <th className="pb-2 font-medium">Weight</th>
                    <th className="pb-2 text-right font-medium">Points</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {a.components.map((c) => (
                    <tr key={c.name}>
                      <td className="py-2 text-slate-800">{c.label}</td>
                      <td className="tabular py-2">{c.score ?? 'not measured'}</td>
                      <td className="tabular py-2 text-slate-600">{c.effective_weight}%</td>
                      <td className="tabular py-2 text-right font-medium">{c.contribution}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-3 text-xs text-slate-500">
                Strong fit from 70, possible fit from 50. Requirement coverage counts each requirement's best evidence, from
                the CV or the interview.
              </p>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {KINDS.map((kind) => (
              <FindingsCard key={kind} kind={kind} findings={a.findings.filter((f) => f.kind === kind)} />
            ))}
          </div>

          <Card title="Requirements" description="What the CV screening and the interview each showed.">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="pb-2 font-medium">Requirement</th>
                    <th className="pb-2 font-medium">CV screening</th>
                    <th className="pb-2 font-medium">Interview</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {a.requirements.map((r) => (
                    <tr key={r.id}>
                      <td className="py-2 pr-3 text-slate-800">
                        {r.text}
                        {r.is_mandatory && <span className="ml-2 text-xs text-slate-500">must-have</span>}
                      </td>
                      <td className="py-2 pr-3">{r.cv_status ? CV_LABEL[r.cv_status] : '–'}</td>
                      <td className="py-2">{INTERVIEW_LABEL[r.interview_status]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <p className="text-xs text-slate-500">
            Written by {a.model_name}
            {a.generated_at && ` on ${formatWhen(a.generated_at, d.interview.timezone_name)}`}. Every finding quotes the
            CV or the interview it rests on.
          </p>
        </>
      )}
        </>
      )}
    </div>
  )
}

function FindingsCard({ kind, findings }: { kind: FindingKind; findings: NonNullable<AssessmentReport['assessment']>['findings'] }) {
  return (
    <Card title={KIND_LABEL[kind]}>
      {findings.length === 0 ? (
        <p className="text-sm text-slate-500">None found with evidence.</p>
      ) : (
        <ul className="space-y-4">
          {findings.map((f, i) => (
            <li key={i}>
              <p className="text-sm text-slate-900">{f.text}</p>
              <ul className="mt-1.5 space-y-1">
                {f.evidence.map((e, j) => (
                  <EvidenceLine key={j} e={e} />
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function EvidenceLine({ e }: { e: AssessmentEvidence }) {
  const where =
    e.type === 'cv'
      ? 'CV'
      : e.type === 'transcript'
        ? `${e.speaker_role === 'candidate' ? 'Candidate' : 'Interviewer'}, ${elapsed(e.start_offset_ms ?? 0)}`
        : 'Requirement'
  return (
    <li className="text-xs text-slate-600">
      <span className={cn('mr-1 font-medium', e.type === 'score' ? 'text-amber-800' : 'text-slate-500')}>{where}:</span>
      {e.type === 'score' ? e.quote : <mark className="rounded-sm bg-marker px-0.5 text-slate-900">{e.quote}</mark>}
    </li>
  )
}
