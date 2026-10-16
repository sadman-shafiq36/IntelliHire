// one candidate's score: how each dimension was reached, what it adds, and the cv lines behind it

import { useMutation, useQuery } from '@tanstack/react-query'
import { CalendarCheck, Check, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { api, ApiError } from '@/api/client'
import type { DimensionScore, ScoreBreakdown } from '@/api/types'
import { Alert, Badge, Button, Spinner } from '@/components/ui'
import { DIMENSION_HELP, DIMENSION_LABEL } from '@/lib/scoring'
import { DeleteCandidateButton } from '@/pages/DeleteCandidateButton'

const RESULT = { met: 'Met', partially_met: 'Partly met', unmet: 'Not met' } as const

export function ScoreBreakdownDrawer({
  jobId,
  candidateId,
  onClose,
  onChanged,
}: {
  jobId: string
  candidateId: number
  onClose: () => void
  onChanged: () => void
}) {
  const closeButton = useRef<HTMLButtonElement>(null)
  const detail = useQuery({
    queryKey: ['scoring-candidate', jobId, candidateId],
    queryFn: () => api.get<ScoreBreakdown>(`/api/v1/jobs/${jobId}/scoring/${candidateId}`),
  })

  useEffect(() => {
    closeButton.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const select = useMutation({
    mutationFn: (selected: boolean) =>
      api.patch(`/api/v1/jobs/${jobId}/scoring/${candidateId}`, { selected_for_interview: selected }),
    onSuccess: () => {
      void detail.refetch()
      onChanged()
    },
  })

  const d = detail.data

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-labelledby="score-title">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} aria-hidden="true" />
      <div className="relative flex h-full w-full max-w-2xl flex-col overflow-hidden bg-slate-50 shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-6 py-5">
          <div className="min-w-0">
            <h2 id="score-title" className="truncate text-xl font-semibold text-slate-900">
              {d?.candidate.full_name ?? 'Candidate'}
            </h2>
            {d && (
              <p className="mt-1 text-sm text-slate-600">
                {d.rank ? `#${d.rank} by score` : 'Not in the score ranking'}
                {d.is_shortlisted && ', on the shortlist'}
              </p>
            )}
          </div>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            className="rounded-md p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            aria-label="Close"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-6">
          {detail.isLoading && <Spinner label="Loading the score" />}
          {detail.error && (
            <Alert tone="error">
              {detail.error instanceof ApiError ? detail.error.message : 'Could not load.'}
            </Alert>
          )}

          {d && (
            <>
              <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
                <div>
                  <p className="tabular text-5xl font-semibold tracking-[-0.03em] text-slate-900">
                    {Math.round(d.aggregate_score)}
                  </p>
                  <p className="text-sm text-slate-600">overall score of 100</p>
                </div>
                <div className="ml-auto">
                  {d.selected_at ? (
                    <Button variant="outline" size="sm" disabled={select.isPending} onClick={() => select.mutate(false)}>
                      <CalendarCheck aria-hidden="true" className="h-4 w-4" />
                      Selected for interview, undo
                    </Button>
                  ) : (
                    <Button size="sm" disabled={select.isPending || !d.rank} onClick={() => select.mutate(true)}>
                      <CalendarCheck aria-hidden="true" className="h-4 w-4" />
                      Select for interview
                    </Button>
                  )}
                </div>
              </div>
              {select.error && (
                <Alert tone="error">
                  {select.error instanceof ApiError ? select.error.message : 'That change was not saved.'}
                </Alert>
              )}
              {d.stale && (
                <Alert tone="warning">
                  This candidate was screened again after this score was made. Score again to bring it up to date.
                </Alert>
              )}
              {d.warnings.map((w) => (
                <Alert key={w} tone="warning">
                  {w}
                </Alert>
              ))}

              <ContributionTable data={d} />

              <div className="space-y-3">
                {d.dimensions.map((dim) => (
                  <DimensionCard key={dim.dimension} dim={dim} />
                ))}
              </div>

              <div className="space-y-3 border-t border-slate-200 pt-4">
                <p className="text-xs leading-relaxed text-slate-500">
                  Scored {d.computed_at && new Date(d.computed_at).toLocaleString()} by {d.model_name} (
                  {d.prompt_version}). Each score starts from the checked screening evidence; the AI may
                  move it by at most 15 points, and only with a quote found word for word in the CV.
                  Scores are stored, so opening this page never re-runs the AI.
                </p>
                <DeleteCandidateButton candidateId={d.candidate.id} name={d.candidate.full_name} onDeleted={onClose} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ContributionTable({ data }: { data: ScoreBreakdown }) {
  return (
    <table className="w-full overflow-hidden rounded-xl border border-slate-200 bg-white text-sm">
      <caption className="sr-only">How the overall score is made up</caption>
      <thead className="bg-slate-50 text-left text-xs text-slate-600">
        <tr>
          <th scope="col" className="px-4 py-2 font-medium">Dimension</th>
          <th scope="col" className="px-4 py-2 text-right font-medium">Score</th>
          <th scope="col" className="px-4 py-2 text-right font-medium">Weight</th>
          <th scope="col" className="px-4 py-2 text-right font-medium">Adds</th>
        </tr>
      </thead>
      <tbody className="tabular divide-y divide-slate-100">
        {data.dimensions.map((dim) => (
          <tr key={dim.dimension}>
            <th scope="row" className="px-4 py-2 text-left font-normal text-slate-800">
              {DIMENSION_LABEL[dim.dimension]}
            </th>
            <td className="px-4 py-2 text-right text-slate-900">{Math.round(dim.score)}</td>
            <td className="px-4 py-2 text-right text-slate-600">{Math.round(dim.weight)}%</td>
            <td className="px-4 py-2 text-right font-medium text-slate-900">{dim.contribution.toFixed(1)}</td>
          </tr>
        ))}
        {data.not_scored.map((name) => (
          <tr key={name}>
            <th scope="row" className="px-4 py-2 text-left font-normal text-slate-500">
              {DIMENSION_LABEL[name]}
            </th>
            <td colSpan={3} className="px-4 py-2 text-right text-xs text-slate-500">
              not scored, its weight is shared by the others
            </td>
          </tr>
        ))}
        <tr className="bg-slate-50">
          <th scope="row" className="px-4 py-2 text-left font-medium text-slate-900">Overall</th>
          <td colSpan={3} className="px-4 py-2 text-right font-semibold text-slate-900">
            {data.aggregate_score.toFixed(1)}
          </td>
        </tr>
      </tbody>
    </table>
  )
}

function DimensionCard({ dim }: { dim: DimensionScore }) {
  const det = dim.details
  const adjusted = dim.adjustment !== 0
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-labelledby={`dim-${dim.dimension}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`dim-${dim.dimension}`} className="font-medium text-slate-900">
            {DIMENSION_LABEL[dim.dimension]}
          </h3>
          <p className="text-xs text-slate-500">{DIMENSION_HELP[dim.dimension]}</p>
        </div>
        <p className="tabular shrink-0 text-2xl font-semibold text-slate-900">{Math.round(dim.score)}</p>
      </div>

      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
        <div className="h-full rounded-full bg-brand-600" style={{ width: `${dim.score}%` }} />
      </div>
      {dim.dimension !== 'jd_match' && dim.base_score !== null && (
        <p className="mt-2 text-xs text-slate-600">
          Evidence {Math.round(dim.base_score)}
          {adjusted ? `, AI ${dim.adjustment > 0 ? '+' : '−'}${Math.abs(dim.adjustment)}` : ', no AI change'}
        </p>
      )}

      {dim.justification && <p className="mt-3 text-[0.9375rem] leading-relaxed text-slate-800">{dim.justification}</p>}

      {det.quotes && det.quotes.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {det.quotes.map((q) => (
            <li key={q} className="text-sm leading-relaxed text-slate-800">
              <mark className="rounded-sm bg-marker px-1 py-0.5 text-slate-900 [box-decoration-break:clone]">{q}</mark>
            </li>
          ))}
          <li className="flex items-center gap-1.5 text-xs text-slate-500">
            <Check aria-hidden="true" className="h-3.5 w-3.5 text-brand-600" />
            Found in the CV
          </li>
        </ul>
      )}

      {det.requirements && det.requirements.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-slate-600 hover:text-slate-900">{det.basis}</summary>
          <ul className="mt-2 space-y-1.5">
            {det.requirements.map((r) => (
              <li key={`${r.requirement_id}-${r.text}`} className="flex items-start justify-between gap-3">
                <span className="text-slate-800">{r.text}</span>
                <Badge tone={r.status === 'met' ? 'success' : r.status === 'partially_met' ? 'warning' : 'neutral'}>
                  {RESULT[r.status]}
                </Badge>
              </li>
            ))}
          </ul>
        </details>
      )}

      {dim.dimension === 'jd_match' && (det.found || det.missing) && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {det.found?.map((t) => (
            <span key={t} className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-xs text-brand-800">
              <Check aria-hidden="true" className="h-3 w-3" />
              {t}
            </span>
          ))}
          {det.missing?.map((t) => (
            <span key={t} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500 line-through decoration-slate-400">
              <span className="sr-only">missing: </span>
              {t}
            </span>
          ))}
        </div>
      )}
    </section>
  )
}
