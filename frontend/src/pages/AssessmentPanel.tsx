// the job's Assessment step: every assessed candidate with their position and component
// scores, and a side-by-side comparison of the ones ticked

import { useQuery } from '@tanstack/react-query'
import { Columns3, FileText } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { AssessmentComparison, CoverageStatus, FindingKind } from '@/api/types'
import { Alert, Badge, Button, Card, EmptyState, Spinner } from '@/components/ui'
import { capitalise, KIND_LABEL, POSITION_TONE } from '@/pages/AssessmentPage'

const MAX_COMPARED = 4
const COMPONENT_LABEL = {
  cv_fit: 'CV fit',
  interview_answers: 'Interview answers',
  requirement_coverage: 'Requirement coverage',
} as const
const EVIDENCE: Record<string, string> = {
  met: 'CV: met',
  partially_met: 'CV: partly',
  unmet: 'CV: not met',
}
const SHOWN: Record<CoverageStatus, string> = {
  covered: 'interview: covered',
  partially_covered: 'interview: partly',
  not_explored: 'interview: not explored',
}

export function AssessmentPanel({ jobId }: { jobId: number }) {
  const [picked, setPicked] = useState<number[]>([])
  const [comparing, setComparing] = useState(false)

  const data = useQuery({
    queryKey: ['assessments', jobId],
    queryFn: () => api.get<AssessmentComparison>(`/api/v1/jobs/${jobId}/assessments`),
  })

  if (data.isLoading) return <Spinner label="Loading the assessments" />
  if (data.error || !data.data)
    return <Alert tone="error">{data.error instanceof ApiError ? data.error.message : 'Could not load the assessments.'}</Alert>

  const d = data.data
  if (d.candidates.length === 0)
    return (
      <EmptyState title="No one assessed yet">
        A consolidated report is written for each candidate once their interview has ended and been analysed. It
        combines the CV screening, the scores and the interview.
      </EmptyState>
    )

  const chosen = d.candidates.filter((c) => picked.includes(c.candidate_id))

  function toggle(id: number) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < MAX_COMPARED ? [...p, id] : p))
  }

  return (
    <div className="space-y-6">
      <Card
        title="Assessed candidates"
        description={`Overall = CV fit ${d.weights.cv_fit}%, interview answers ${d.weights.interview_answers}%, requirement coverage ${d.weights.requirement_coverage}%. Tick up to ${MAX_COMPARED} to compare.`}
        actions={
          <Button size="sm" disabled={chosen.length < 2} onClick={() => setComparing(true)}>
            <Columns3 aria-hidden="true" className="h-4 w-4" />
            Compare {chosen.length > 1 ? chosen.length : ''}
          </Button>
        }
      >
        <ul className="divide-y divide-slate-100">
          {d.candidates.map((c) => (
            <li key={c.candidate_id} className="flex flex-wrap items-center gap-3 py-3">
              <input
                type="checkbox"
                aria-label={`Compare ${c.full_name}`}
                checked={picked.includes(c.candidate_id)}
                disabled={!picked.includes(c.candidate_id) && picked.length >= MAX_COMPARED}
                onChange={() => toggle(c.candidate_id)}
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-slate-900">{c.full_name}</p>
                <p className="text-xs text-slate-500">
                  {Object.entries(COMPONENT_LABEL)
                    .map(([k, label]) => `${label} ${c.components[k as keyof typeof COMPONENT_LABEL] ?? '–'}`)
                    .join(' · ')}
                </p>
              </div>
              {c.position && <Badge tone={POSITION_TONE[c.position]}>{capitalise(c.position_label)}</Badge>}
              <span className="tabular w-12 text-right text-lg font-semibold text-slate-900">{c.overall_score}</span>
              <Link
                to={`/interviews/${c.interview_id}/assessment`}
                className="inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:underline"
              >
                <FileText aria-hidden="true" className="h-4 w-4" />
                Report
              </Link>
            </li>
          ))}
        </ul>
      </Card>

      {comparing && chosen.length >= 2 && (
        <Card title="Side by side" actions={<Button size="sm" variant="ghost" onClick={() => setComparing(false)}>Close</Button>}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] table-fixed text-sm">
              <thead>
                <tr className="text-left align-bottom">
                  <th className="w-48 pb-3 text-xs font-medium text-slate-500" />
                  {chosen.map((c) => (
                    <th key={c.candidate_id} className="pb-3 pr-3 font-semibold text-slate-900">
                      {c.full_name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 align-top">
                <Row label="Position">
                  {chosen.map((c) => (
                    <td key={c.candidate_id} className="py-2 pr-3">
                      {c.position && <Badge tone={POSITION_TONE[c.position]}>{capitalise(c.position_label)}</Badge>}
                    </td>
                  ))}
                </Row>
                <Row label="Overall">
                  {chosen.map((c) => (
                    <td key={c.candidate_id} className="tabular py-2 pr-3 text-lg font-semibold">
                      {c.overall_score}
                    </td>
                  ))}
                </Row>
                {(Object.keys(COMPONENT_LABEL) as (keyof typeof COMPONENT_LABEL)[]).map((k) => (
                  <Row key={k} label={COMPONENT_LABEL[k]}>
                    {chosen.map((c) => (
                      <td key={c.candidate_id} className="tabular py-2 pr-3">
                        {c.components[k] ?? '–'}
                      </td>
                    ))}
                  </Row>
                ))}
                {d.requirements.map((r) => (
                  <Row key={r.id} label={r.text} hint={r.must_have ? 'must-have' : undefined}>
                    {chosen.map((c) => {
                      const e = c.requirements[String(r.id)]
                      return (
                        <td key={c.candidate_id} className="py-2 pr-3 text-xs text-slate-600">
                          {e ? (
                            <>
                              {EVIDENCE[e.cv ?? ''] ?? 'CV: –'}
                              <br />
                              {e.interview ? SHOWN[e.interview] : 'interview: –'}
                            </>
                          ) : (
                            '–'
                          )}
                        </td>
                      )
                    })}
                  </Row>
                ))}
                {(Object.keys(KIND_LABEL) as FindingKind[]).map((kind) => (
                  <Row key={kind} label={KIND_LABEL[kind]}>
                    {chosen.map((c) => (
                      <td key={c.candidate_id} className="py-2 pr-3">
                        {c.findings[kind].length === 0 ? (
                          <span className="text-xs text-slate-400">none</span>
                        ) : (
                          <ul className="list-disc space-y-1 pl-4 text-xs text-slate-700">
                            {c.findings[kind].map((t) => (
                              <li key={t}>{t}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                    ))}
                  </Row>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <tr>
      <th scope="row" className="py-2 pr-3 text-left text-xs font-medium text-slate-600">
        {label}
        {hint && <span className="block font-normal text-slate-400">{hint}</span>}
      </th>
      {children}
    </tr>
  )
}
