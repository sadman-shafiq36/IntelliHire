// the candidate feedback composer: drafting a letter from the stored evidence, the evidence
// each point rests on, what the guardrails found, and a person's review. nothing is sent
// until someone approves it and presses send

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, MailCheck, Pencil, Plus, RefreshCw, Send, Sparkles, Trash2, X } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type {
  FeedbackDetail,
  FeedbackEvidence,
  FeedbackNote,
  FeedbackOutcome,
  FeedbackStatus,
  FeedbackSummary,
  InterviewFeedback,
} from '@/api/types'
import { Alert, Badge, Button, Card, EmptyState, inputClass, ProgressBar, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'

const STATUS: Record<FeedbackStatus, [string, 'neutral' | 'info' | 'success' | 'warning' | 'error']> = {
  new: ['Not drafted yet', 'neutral'],
  drafting: ['Drafting', 'info'],
  in_review: ['Waiting for review', 'warning'],
  approved: ['Approved', 'success'],
  discarded: ['Discarded', 'neutral'],
  sending: ['Sending', 'info'],
  sent: ['Sent', 'success'],
  failed: ['Failed', 'error'],
}

export const OUTCOME_LABEL: Record<FeedbackOutcome, string> = {
  not_progressing: 'Not progressing',
  on_hold: 'On hold',
  next_round: 'Next round',
}

const KIND_LABEL = { strength: 'Strength', development: 'To strengthen', explore_next: 'To explore next' }
const CHANNEL_LABEL = { gmail: 'by Gmail', smtp: 'by email', outbox: 'to the local outbox' }

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}

export function FeedbackStatusBadge({ status }: { status: FeedbackStatus }) {
  const [label, tone] = STATUS[status]
  return <Badge tone={tone}>{label}</Badge>
}

// --- one letter under review -------------------------------------------------------

export function FeedbackReview({ feedbackId, onChanged }: { feedbackId: number; onChanged?: () => void }) {
  const queryClient = useQueryClient()
  const key = ['feedback', feedbackId]
  const [runId, setRunId] = useState<string | null>(null)
  const progress = useBackgroundJob(runId)
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [instruction, setInstruction] = useState('')

  const data = useQuery({
    queryKey: key,
    queryFn: () => api.get<FeedbackDetail>(`/api/v1/feedback/${feedbackId}`),
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'drafting' || s === 'sending' ? 3000 : false
    },
  })

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: key })
    onChanged?.()
  }

  useEffect(() => {
    if (isDone(progress.data)) refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.state])

  const review = useMutation({
    mutationFn: (b: { action: string; subject?: string; body?: string; instruction?: string }) =>
      api.post<{ status: FeedbackStatus; background_job_id: string | null }>(
        `/api/v1/feedback/${feedbackId}/review`,
        b,
      ),
    onSuccess: (r) => {
      setEditing(false)
      setInstruction('')
      if (r.background_job_id) setRunId(r.background_job_id)
      refresh()
    },
  })

  const send = useMutation({
    mutationFn: () => api.post<FeedbackDetail>(`/api/v1/feedback/${feedbackId}/send`),
    onSuccess: refresh,
  })

  if (data.isLoading) return <Spinner label="Loading the feedback" />
  if (data.error || !data.data) return <Alert tone="error">{message(data.error, 'Could not load the feedback.')}</Alert>

  const d = data.data
  const working = d.status === 'drafting' || (runId !== null && !isDone(progress.data))
  const g = d.guardrail
  const failedRules = g?.rules.filter((r) => !r.passed) ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <FeedbackStatusBadge status={d.status} />
        <Badge tone="neutral">{d.stage === 'screening' ? 'Screening' : 'Interview'}</Badge>
        <Badge tone="neutral">{OUTCOME_LABEL[d.outcome]}</Badge>
        {d.sent_at && d.send_channel && (
          <span className="text-sm text-slate-600">
            Sent {CHANNEL_LABEL[d.send_channel]} on {new Date(d.sent_at).toLocaleString()}
          </span>
        )}
      </div>

      {working && (
        <Alert tone="info">
          <Spinner label={progress.data?.message || 'Writing and checking the letter. This takes a few seconds.'} />
        </Alert>
      )}
      {d.status === 'failed' && <Alert tone="error">{d.message || 'The feedback could not be written.'}</Alert>}
      {d.fallback_message && !working && (
        <Alert tone="info">{d.fallback_message} The generic, polite letter below says nothing specific.</Alert>
      )}
      {d.warnings.map((w) => (
        <Alert key={w} tone="warning">
          {w}
        </Alert>
      ))}
      {review.error && <Alert tone="error">{message(review.error, 'That action did not go through.')}</Alert>}
      {send.error && <Alert tone="error">{message(send.error, 'The feedback could not be sent.')}</Alert>}
      {g && !g.passed && !working && (
        <Alert tone="error">
          The checks found {failedRules.length === 1 ? 'a problem' : 'problems'}: {failedRules.map((r) => r.label.toLowerCase()).join('; ')}.
          Edit the letter or regenerate it; it can't be approved like this.
        </Alert>
      )}

      {d.draft && !working && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <Card
            title="Letter"
            description={d.draft.edited ? 'Edited by a reviewer, then checked again.' : 'Drafted from the evidence; the decision sentence is fixed wording.'}
          >
            {editing ? (
              <div className="space-y-3">
                <label className="block space-y-1.5">
                  <span className="text-sm font-medium text-slate-800">Subject</span>
                  <input className={cn(inputClass, 'border-slate-300')} value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
                </label>
                <label className="block space-y-1.5">
                  <span className="text-sm font-medium text-slate-800">Letter</span>
                  <textarea
                    className={cn(inputClass, 'min-h-[17.5rem] border-slate-300 font-[inherit]')}
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    maxLength={6000}
                  />
                </label>
                <p className="text-xs text-slate-500">
                  Keep {'{candidate_name}'} and {'{signature}'}; they are filled in when it is sent. Your edit is checked by the same rules.
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button disabled={review.isPending} onClick={() => review.mutate({ action: 'edit', subject, body })}>
                    <Check aria-hidden="true" className="h-4 w-4" />
                    Save and check
                  </Button>
                  <Button variant="outline" onClick={() => setEditing(false)}>
                    <X aria-hidden="true" className="h-4 w-4" />
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-sm">
                  <span className="text-slate-500">Subject: </span>
                  <span className="font-medium text-slate-900">{d.draft.subject}</span>
                </p>
                <div className="whitespace-pre-line rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed text-slate-800">
                  <LetterBody body={d.draft.body} />
                </div>
              </div>
            )}

            {!editing && d.status === 'in_review' && (
              <div className="mt-5 space-y-3 border-t border-slate-100 pt-4">
                <div className="flex flex-wrap gap-2">
                  <Button disabled={!g?.passed || review.isPending} onClick={() => review.mutate({ action: 'approve' })}>
                    <Check aria-hidden="true" className="h-4 w-4" />
                    Approve
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setSubject(d.draft?.subject ?? '')
                      setBody(d.draft?.body ?? '')
                      setEditing(true)
                    }}
                  >
                    <Pencil aria-hidden="true" className="h-4 w-4" />
                    Edit
                  </Button>
                  <Button
                    variant="outline"
                    disabled={review.isPending}
                    onClick={() => {
                      if (window.confirm('Discard this letter? Nothing is sent.')) review.mutate({ action: 'discard' })
                    }}
                  >
                    <Trash2 aria-hidden="true" className="h-4 w-4" />
                    Discard
                  </Button>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    className={cn(inputClass, 'min-w-0 flex-1 border-slate-300')}
                    placeholder="Optional: what to change when regenerating"
                    aria-label="What to change when regenerating"
                    value={instruction}
                    maxLength={300}
                    onChange={(e) => setInstruction(e.target.value)}
                  />
                  <Button
                    variant="outline"
                    disabled={review.isPending}
                    onClick={() => review.mutate({ action: 'regenerate', instruction: instruction || undefined })}
                  >
                    <RefreshCw aria-hidden="true" className="h-4 w-4" />
                    Regenerate
                  </Button>
                </div>
              </div>
            )}
            {d.status === 'approved' && (
              <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
                <Button
                  disabled={send.isPending}
                  onClick={() => {
                    const where = d.has_email
                      ? `Send this letter to ${d.candidate_name}? It can only be sent once.`
                      : `${d.candidate_name} has no email address on file. Save the letter to the outbox?`
                    if (window.confirm(where)) send.mutate()
                  }}
                >
                  <Send aria-hidden="true" className="h-4 w-4" />
                  Send
                </Button>
                <span className="text-xs text-slate-500">
                  Sent through the connected email; if that can't be reached it is saved to the local outbox.
                </span>
              </div>
            )}
          </Card>

          <div className="space-y-4">
            {g && <GuardrailCard guardrail={g} />}
            <EvidenceCard d={d} />
            {(d.notes.length > 0 || d.excluded_notes.length > 0) && <NotesCard d={d} />}
          </div>
        </div>
      )}
      {d.model_name && d.draft && (
        <p className="text-xs text-slate-500">
          Written by {d.model_name}. Every point names the requirement and the stored result it rests on; nothing about
          scores, rankings or other candidates is ever included.
        </p>
      )}
    </div>
  )
}

function LetterBody({ body }: { body: string }) {
  const parts = body.split(/(\{candidate_name\}|\{signature\})/)
  return (
    <>
      {parts.map((p, i) =>
        p === '{candidate_name}' || p === '{signature}' ? (
          <span key={i} className="rounded bg-slate-200 px-1 text-xs text-slate-700">
            {p === '{candidate_name}' ? "candidate's name" : 'your signature'}
          </span>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  )
}

function GuardrailCard({ guardrail: g }: { guardrail: NonNullable<FeedbackDetail['guardrail']> }) {
  const judge = {
    passed: 'The AI review found nothing.',
    failed: 'The AI review found problems.',
    not_run: "The AI review couldn't run; only the fixed rules were checked.",
    skipped: 'Fixed wording; only the rules were needed.',
  }[g.judge.status]
  return (
    <Card
      title="Guardrails"
      description={`${g.checked === 'edit' ? 'Your edit' : g.checked === 'fallback' ? 'The generic letter' : `Draft ${g.attempts}`} against the rules. ${judge}`}
    >
      <ul className="space-y-2">
        {g.rules.map((r) => (
          <li key={r.rule} className="text-sm">
            <span className="flex items-center gap-2">
              {r.passed ? (
                <Check aria-hidden="true" className="h-4 w-4 shrink-0 text-brand-600" />
              ) : (
                <X aria-hidden="true" className="h-4 w-4 shrink-0 text-red-700" />
              )}
              <span className={r.passed ? 'text-slate-700' : 'font-medium text-red-800'}>{r.label}</span>
            </span>
            {!r.passed && (
              <ul className="ml-6 mt-1 space-y-0.5 text-xs text-slate-600">
                {r.found.map((f) => (
                  <li key={f}>Found: “{f}”</li>
                ))}
                {r.judge.map((j, i) => (
                  <li key={i}>
                    “{j.quote}”: {j.explanation}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function describe(e: FeedbackEvidence): { where: string; text?: string | null; quote?: string | null } {
  if (!e.found) return { where: 'Stored result no longer exists' }
  switch (e.type) {
    case 'screening_result':
      return {
        where: `CV screening: ${e.status === 'met' ? 'met' : e.status === 'partially_met' ? 'partly met' : 'not met'}`,
        text: e.text,
        quote: e.verified ? e.quote : null,
      }
    case 'qa_pair':
      return { where: `Interview question ${e.sequence}: ${e.question}`, text: e.text }
    case 'assessment_finding':
      return { where: 'Consolidated assessment', text: e.text }
    case 'score_dimension':
      return { where: 'Score justification', text: e.text }
  }
}

function EvidenceLine({ e }: { e: FeedbackEvidence }) {
  const { where, text, quote } = describe(e)
  return (
    <li className="text-xs text-slate-600">
      <span className="font-medium text-slate-500">{where}</span>
      {quote && (
        <>
          {' '}
          <mark className="rounded-sm bg-marker px-0.5 text-slate-900">{quote}</mark>
        </>
      )}
      {text && <span className="block">{text.replace(/\s*\[(T|S)\d+\]/g, '')}</span>}
    </li>
  )
}

function EvidenceCard({ d }: { d: FeedbackDetail }) {
  return (
    <Card title="Evidence" description="What the letter may talk about, chosen by code from stored results.">
      {d.points.length === 0 ? (
        <p className="text-sm text-slate-500">No points: the letter is the generic one.</p>
      ) : (
        <ol className="space-y-4">
          {d.points.map((p) => (
            <li key={p.id}>
              <p className="text-sm text-slate-900">
                <span className="mr-2 text-xs font-medium text-slate-500">
                  {p.id} · {KIND_LABEL[p.kind]}
                </span>
                {p.requirement}
              </p>
              <ul className="mt-1 space-y-1.5 border-l-2 border-slate-100 pl-3">
                <EvidenceLine e={p.evidence_ref} />
                {p.support.map((s) => (
                  <EvidenceLine key={`${s.type}-${s.id}`} e={s} />
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

function NotesCard({ d }: { d: FeedbackDetail }) {
  const excluded = new Set(d.excluded_notes.map((n) => n.id))
  return (
    <Card title="Notes from the team" description="Notes that mention personal characteristics or aren't about the work are left out.">
      <ul className="space-y-2">
        {d.notes.map((n) => {
          const out = d.excluded_notes.find((x) => x.id === n.id)
          return (
            <li key={n.id} className="text-sm">
              <span className={cn(excluded.has(n.id) ? 'text-slate-500 line-through' : 'text-slate-800')}>{n.text}</span>
              <span className="block text-xs text-slate-500">
                {n.author === 'interviewer' ? 'Interviewer' : 'Recruiter'} ·{' '}
                {out ? <span className="text-red-800">Left out. {out.reason}</span> : 'Used to choose emphasis'}
              </span>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

// --- interview-stage feedback, on the assessment page ------------------------------

export function InterviewFeedbackPanel({ interviewId }: { interviewId: string }) {
  const queryClient = useQueryClient()
  const key = ['interview-feedback', interviewId]
  const [outcome, setOutcome] = useState<FeedbackOutcome | null>(null)
  const [notes, setNotes] = useState<Omit<FeedbackNote, 'id'>[] | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const progress = useBackgroundJob(runId)

  const data = useQuery({
    queryKey: key,
    queryFn: () => api.get<InterviewFeedback>(`/api/v1/interviews/${interviewId}/feedback`),
  })

  useEffect(() => {
    if (isDone(progress.data)) void queryClient.invalidateQueries({ queryKey: key })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.state])

  const f = data.data?.feedback
  const shownNotes = notes ?? f?.notes.map(({ author, text }) => ({ author, text })) ?? []
  const chosen = outcome ?? (f && f.status !== 'new' ? f.outcome : null)

  const generate = useMutation({
    mutationFn: () =>
      api.post<{ feedback_id: number; background_job_id: string }>(`/api/v1/candidates/${data.data!.candidate_id}/feedback`, {
        job_id: data.data!.job_id,
        stage: 'interview',
        interview_id: data.data!.interview_id,
        outcome: chosen,
        notes: shownNotes.filter((n) => n.text.trim()),
      }),
    onSuccess: (r) => {
      setRunId(r.background_job_id)
      setNotes(null)
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })

  if (data.isLoading) return <Spinner label="Loading feedback" />
  if (data.error || !data.data) return <Alert tone="error">{message(data.error, 'Could not load feedback.')}</Alert>
  if (!data.data.assessment_ready)
    return (
      <EmptyState icon={<MailCheck className="h-8 w-8" aria-hidden="true" />} title="Not ready yet">
        Feedback to the candidate is written from the consolidated assessment, so the report has to be written first.
      </EmptyState>
    )

  const locked = f && ['drafting', 'approved', 'sending', 'sent'].includes(f.status)

  return (
    <div className="space-y-6">
      {!locked && (
        <Card
          title={f ? 'Write the feedback again' : 'Write feedback to the candidate'}
          description="Choose the outcome yourself; the AI never decides it. The letter only uses the requirements, the Q&A analysis and the assessment."
        >
          <fieldset>
            <legend className="text-sm font-medium text-slate-800">Outcome</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {(Object.keys(OUTCOME_LABEL) as FeedbackOutcome[]).map((o) => (
                <label
                  key={o}
                  className={cn(
                    'cursor-pointer rounded-lg border px-3 py-1.5 text-sm',
                    chosen === o ? 'border-brand-600 bg-brand-50 text-brand-800' : 'border-slate-300 text-slate-700 hover:border-slate-400',
                  )}
                >
                  <input type="radio" name="outcome" className="sr-only" checked={chosen === o} onChange={() => setOutcome(o)} />
                  {OUTCOME_LABEL[o]}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-5 space-y-2">
            <p className="text-sm font-medium text-slate-800">Notes from the team (optional)</p>
            <p className="text-xs text-slate-500">
              Checked before use: anything about age, family, background, health, accent or "fit" is left out.
            </p>
            {shownNotes.map((n, i) => (
              <div key={i} className="flex flex-wrap items-start gap-2">
                <select
                  aria-label="Who wrote this note"
                  className={cn(inputClass, 'w-auto border-slate-300')}
                  value={n.author}
                  onChange={(e) =>
                    setNotes(shownNotes.map((x, j) => (j === i ? { ...x, author: e.target.value as FeedbackNote['author'] } : x)))
                  }
                >
                  <option value="interviewer">Interviewer</option>
                  <option value="recruiter">Recruiter</option>
                </select>
                <input
                  aria-label="Note"
                  className={cn(inputClass, 'min-w-0 flex-1 border-slate-300')}
                  value={n.text}
                  maxLength={1000}
                  onChange={(e) => setNotes(shownNotes.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                />
                <Button variant="outline" aria-label="Remove note" onClick={() => setNotes(shownNotes.filter((_, j) => j !== i))}>
                  <X aria-hidden="true" className="h-4 w-4" />
                </Button>
              </div>
            ))}
            {shownNotes.length < 10 && (
              <Button variant="outline" onClick={() => setNotes([...shownNotes, { author: 'interviewer', text: '' }])}>
                <Plus aria-hidden="true" className="h-4 w-4" />
                Add a note
              </Button>
            )}
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <Button disabled={!chosen || generate.isPending} onClick={() => generate.mutate()}>
              <Sparkles aria-hidden="true" className="h-4 w-4" />
              {f && f.status !== 'new' ? 'Write again' : 'Write feedback'}
            </Button>
            {!chosen && <span className="text-xs text-slate-500">Choose an outcome first.</span>}
          </div>
          {generate.error && (
            <div className="mt-3">
              <Alert tone="error">{message(generate.error, 'Could not start.')}</Alert>
            </div>
          )}
        </Card>
      )}

      {f && f.status !== 'new' && (
        <FeedbackReview feedbackId={f.id} onChanged={() => void queryClient.invalidateQueries({ queryKey: key })} />
      )}
    </div>
  )
}

// --- screening-stage feedback, on the scoring step ---------------------------------

export function useScreeningFeedback(jobId: string) {
  return useQuery({
    queryKey: ['feedback-queue', jobId],
    queryFn: () => api.get<FeedbackSummary[]>(`/api/v1/jobs/${jobId}/feedback?stage=screening`),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'drafting' || r.status === 'sending') ? 3000 : false),
  })
}

export function ScreeningFeedbackQueue({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient()
  const rows = useScreeningFeedback(jobId)
  const [open, setOpen] = useState<number | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const progress = useBackgroundJob(runId)

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['feedback-queue', jobId] })
  }

  useEffect(() => {
    if (progress.data) refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.completed, progress.data?.state])

  const batch = useMutation({
    mutationFn: () => api.post<{ background_job_id: string; candidate_count: number }>(`/api/v1/jobs/${jobId}/feedback/screening-batch`),
    onSuccess: (r) => {
      setRunId(r.background_job_id)
      refresh()
    },
  })

  const list = rows.data ?? []
  const waiting = list.filter((r) => r.status === 'new' || r.status === 'failed').length
  const running = runId !== null && !isDone(progress.data)

  return (
    <section aria-labelledby="feedback-queue-heading" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="feedback-queue-heading" className="text-lg font-semibold text-slate-900">
            Feedback review queue
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Short feedback for candidates you mark as not selected for interview. Nobody gets it because of their place in
            the ranking; only your mark puts them here. Each letter is checked and waits for your approval.
          </p>
        </div>
        <Button disabled={waiting === 0 || running || batch.isPending} onClick={() => batch.mutate()}>
          <Sparkles aria-hidden="true" className="h-4 w-4" />
          {waiting > 0 ? `Draft feedback for ${waiting}` : 'All drafted'}
        </Button>
      </div>
      {batch.error && <Alert tone="error">{message(batch.error, 'Drafting could not start.')}</Alert>}
      {running && progress.data && (
        <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4" aria-live="polite">
          <Spinner label={progress.data.message || 'Drafting'} />
          <ProgressBar percent={progress.data.percent} />
        </div>
      )}
      {list.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-6 text-sm text-slate-600">
          Nobody is marked as not selected. Use “Not selected” on a candidate in the score ranking.
        </p>
      ) : (
        <ul className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {list.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                aria-expanded={open === r.id}
                onClick={() => setOpen(open === r.id ? null : r.id)}
                className="flex w-full flex-wrap items-center gap-3 px-5 py-3 text-left hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
              >
                <span className="min-w-0 flex-1 truncate font-medium text-slate-900">{r.candidate_name}</span>
                {r.fallback_reason && <Badge tone="neutral">Generic letter</Badge>}
                {r.status === 'in_review' && (
                  <Badge tone={r.guardrail_passed ? 'success' : 'error'}>
                    {r.guardrail_passed ? 'Checks passed' : 'Checks failed'}
                  </Badge>
                )}
                <FeedbackStatusBadge status={r.status} />
              </button>
              {open === r.id && r.status !== 'new' && (
                <div className="border-t border-slate-100 bg-slate-50/50 px-5 py-4">
                  <FeedbackReview feedbackId={r.id} onChanged={refresh} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
