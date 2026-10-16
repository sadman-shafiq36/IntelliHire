// one candidate's screening: every requirement with its verdict and the cv line behind it

import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, Download, ScanLine, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { api, ApiError } from '@/api/client'
import type { CandidateScreening, MatchStatus, RequirementEvidence } from '@/api/types'
import { Alert, Badge, Button, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'
import { DeleteCandidateButton } from '@/pages/DeleteCandidateButton'

const VERDICT: Record<MatchStatus, { label: string; tone: 'success' | 'warning' | 'neutral' }> = {
  met: { label: 'Met', tone: 'success' },
  partially_met: { label: 'Partly met', tone: 'warning' },
  unmet: { label: 'Not met', tone: 'neutral' },
}

export function CandidateEvidence({
  jobId,
  candidateId,
  onClose,
}: {
  jobId: string
  candidateId: number
  onClose: () => void
}) {
  const closeButton = useRef<HTMLButtonElement>(null)
  const detail = useQuery({
    queryKey: ['screening-candidate', jobId, candidateId],
    queryFn: () =>
      api.get<CandidateScreening>(`/api/v1/jobs/${jobId}/screening/${candidateId}`),
  })

  useEffect(() => {
    closeButton.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const download = useMutation({
    mutationFn: async () => {
      const cv = detail.data?.cv
      if (!cv) return
      const blob = await api.download(`/api/v1/cv-documents/${cv.document_id}/file`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = cv.filename
      a.click()
      URL.revokeObjectURL(url)
    },
  })

  const d = detail.data
  const must = d?.requirements.filter((r) => r.is_mandatory) ?? []
  const rest = d?.requirements.filter((r) => !r.is_mandatory) ?? []

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} aria-hidden="true" />
      <div className="relative flex h-full w-full max-w-2xl flex-col overflow-hidden bg-slate-50 shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-6 py-5">
          <div className="min-w-0">
            <h2 id="evidence-title" className="truncate text-xl font-semibold text-slate-900">
              {d?.candidate.full_name ?? 'Candidate'}
            </h2>
            {d && (
              <p className="mt-1 text-sm text-slate-600">
                {[d.candidate.email, d.candidate.phone, d.candidate.location]
                  .filter(Boolean)
                  .join(', ') || 'No contact details in the CV'}
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
          {detail.isLoading && <Spinner label="Loading the screening" />}
          {detail.error && (
            <Alert tone="error">
              {detail.error instanceof ApiError ? detail.error.message : 'Could not load.'}
            </Alert>
          )}

          {d && (
            <>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <div>
                  <p className="tabular text-4xl font-semibold tracking-[-0.03em] text-slate-900">
                    {Math.round(d.fulfilment_percent)}%
                  </p>
                  <p className="text-sm text-slate-600">of the role's requirements covered</p>
                </div>
                {d.rank && (
                  <div>
                    <p className="tabular text-4xl font-semibold tracking-[-0.03em] text-slate-900">
                      #{d.rank}
                    </p>
                    <p className="text-sm text-slate-600">in the ranking</p>
                  </div>
                )}
                {d.cv && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="ml-auto"
                    onClick={() => download.mutate()}
                    disabled={download.isPending}
                  >
                    <Download aria-hidden="true" className="h-4 w-4" />
                    Open CV
                  </Button>
                )}
              </div>

              {d.exclusion_reason && <Alert tone="warning">{d.exclusion_reason}</Alert>}
              {d.summary && <p className="text-[0.9375rem] leading-relaxed text-slate-800">{d.summary}</p>}

              <EvidenceGroup title="Must-haves" items={must} />
              <EvidenceGroup title="Nice to have" items={rest} />

              <Profile data={d} />

              <p className="border-t border-slate-200 pt-4 text-xs leading-relaxed text-slate-500">
                Screened {d.screened_at && new Date(d.screened_at).toLocaleString()}
                {d.model_name && ` by ${d.model_name}`}
                {d.cv?.used_ocr && '. The CV was a scan, read with text recognition'}. Names,
                emails and phone numbers are removed before the CV is compared with the
                requirements. A quote marked "found in the CV" was checked word for word.
              </p>
              <DeleteCandidateButton
                candidateId={d.candidate.id}
                name={d.candidate.full_name}
                onDeleted={onClose}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function EvidenceGroup({ title, items }: { title: string; items: RequirementEvidence[] }) {
  if (items.length === 0) return null
  return (
    <section>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <ul className="mt-2 space-y-2">
        {items.map((r) => (
          <li key={r.requirement_id} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="font-medium text-slate-900">{r.text}</p>
              <Badge tone={VERDICT[r.status].tone}>{VERDICT[r.status].label}</Badge>
            </div>
            {r.evidence_quote && (
              <blockquote className="mt-3 text-[0.9375rem] leading-relaxed text-slate-800">
                <mark className="rounded-sm bg-marker px-1 py-0.5 text-slate-900 [box-decoration-break:clone]">
                  {r.evidence_quote}
                </mark>
              </blockquote>
            )}
            {r.reasoning && (
              <p className={cn('text-sm text-slate-600', r.evidence_quote ? 'mt-2' : 'mt-1')}>
                {r.reasoning}
              </p>
            )}
            {r.evidence_quote && (
              <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
                {r.evidence_verified ? (
                  <>
                    <Check aria-hidden="true" className="h-3.5 w-3.5 text-brand-600" />
                    Found in the CV
                  </>
                ) : (
                  'Not found word for word in the CV'
                )}
                {r.confidence !== null && ` · confidence ${Math.round(r.confidence * 100)}%`}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Profile({ data }: { data: CandidateScreening }) {
  const p = data.profile
  if (!p || Object.keys(p).length === 0) return null
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <ScanLine aria-hidden="true" className="h-4 w-4 text-slate-500" />
        Read from the CV
      </h3>
      {p.headline && <p className="mt-2 text-sm text-slate-800">{p.headline}</p>}
      {p.total_years_experience ? (
        <p className="mt-1 text-sm text-slate-600">{p.total_years_experience} years of experience</p>
      ) : null}
      {p.skills && p.skills.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {p.skills.map((s) => (
            <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
              {s}
            </span>
          ))}
        </div>
      )}
      {p.work_history && p.work_history.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-slate-500">Experience</p>
          <ul className="mt-1 space-y-1.5">
            {p.work_history.map((w, i) => (
              <li key={i} className="text-sm text-slate-800">
                {w.title}, {w.organisation}{' '}
                <span className="text-slate-500">
                  ({w.start} to {w.end})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.education && p.education.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-slate-500">Education</p>
          <ul className="mt-1 space-y-1">
            {p.education.map((e, i) => (
              <li key={i} className="text-sm text-slate-800">
                {e.qualification}, {e.institution}
                {e.year && <span className="text-slate-500"> ({e.year})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.certifications && p.certifications.length > 0 && (
        <p className="mt-4 text-sm text-slate-800">
          <span className="text-xs font-medium text-slate-500">Certifications </span>
          {p.certifications.join(', ')}
        </p>
      )}
    </section>
  )
}
